from abc import ABC, abstractmethod
import socket, threading, multiprocessing, signal, datetime, time, traceback, sys

class DMSProcess(ABC):

    def __init__(self,hb_grace_period=5.0,hb_timeout=5.0,hb_max_retries=0,interrupt_on=(),logging_enabled=False):
        self.LOGGING_ENABLED = logging_enabled

        signal.signal(signal.SIGTERM, self.trigger) # Process will *always* trigger on SIGTERM.
        if set(interrupt_on).issubset(signal.valid_signals()):
            for signalToTrigger in interrupt_on:
                signal.signal(signalToTrigger, self.trigger)

        # We use *one* socket for the heartbeat; this is passed to both threads (checking-side and acknowledgement side).

        self.lifelineSkt.settimeout(hb_timeout)
        self.HEARTBEAT_MAXIMUM_RETRIES = hb_max_retries
        self.HEARTBEAT_ATTEMPTS_FAILED = 0
        self.HEARTBEAT_GRACE_PERIOD=hb_grace_period

        # NOTE: we have to use threading for this instead of multiprocessing because the latter does not propagate exceptions and signals to parent threads on Windows without
        # joining the thread.
        # This means that we cannot escape the GIL in terms of competition between the lifeline thread and the observer thread; multiprocessing should be used /within/ the latter to optimize resources.

        # This thread handles our 'heartbeat' signal, separate from the function code.
        self.lifelineThread = threading.Thread(target=self.checkSkt)
        self.lifelineThread.start()

    def checkSkt(self):
        while True:
            #print(f"[{self}][{__name__}][{datetime.datetime.now()}] - Entering heartbeat grace period for {self.HEARTBEAT_GRACE_PERIOD} second(s).")
            time.sleep(self.HEARTBEAT_GRACE_PERIOD)
            try:
                self.lifelineSkt.send(b'1')
                #print(f"[{self}][{__name__}][{datetime.datetime.now()}] - Heartbeat sent.")
                # 'pingpong' heartbeat: Observer has to send heartbeat and expects one back from payload.
                #print(f"[{self}][{__name__}][{datetime.datetime.now()}] - Awaiting response...")
                self.lifelineSkt.recv(1)
                #print(f"[{self}][{__name__}][{datetime.datetime.now()}] - Heartbeat received.")
                # TODO: Do any race conditions occur here?
                self.HEARTBEAT_ATTEMPTS_FAILED = 0
            except:
                print(f"[{self}][{__name__}][{datetime.datetime.now()}] - Failed to receive heartbeat.")
                if self.HEARTBEAT_ATTEMPTS_FAILED != self.HEARTBEAT_MAXIMUM_RETRIES:
                    self.HEARTBEAT_ATTEMPTS_FAILED += 1
                    print(f"[{self}][{__name__}][{datetime.datetime.now()}] - Retries left: {self.HEARTBEAT_MAXIMUM_RETRIES-self.HEARTBEAT_ATTEMPTS_FAILED} [{self.HEARTBEAT_ATTEMPTS_FAILED}/{self.HEARTBEAT_MAXIMUM_RETRIES}].")
                    pass
                else:
                    print(f"[{self}][{__name__}][{datetime.datetime.now()}] - Maximum retries exceeded. Closing socket and sending SIGTERM.")
                    self.lifelineSkt.close()
                    signal.raise_signal(signal.SIGTERM)
                    sys.exit(signal.SIGTERM) # Possibly moot
    
    @abstractmethod
    def trigger():
        pass



class obsProcess(DMSProcess):

    def __init__(self,host,port,hb_grace_period=5.0,hb_timeout=5.0,hb_max_retries=5,hs_timeout=5.0,interrupt_on=(),logging_enabled=False,func=False,args=()):
        assert func, f"[{self}][{__name__}][{datetime.datetime.now()}] No observer function provided!"
        try:
            self.lifelineSkt = socket.create_connection((host,port),timeout=hs_timeout)
        except Exception as e:
            print(f"[{self}][{__name__}][{datetime.datetime.now()}] - Failed to initiate connection with payload on {host}:{port}.")
            print(f"[{self}][{__name__}][{datetime.datetime.now()}] Exception: {e}.")
            sys.exit(404)
        
        print(f"[{self}][{__name__}][{datetime.datetime.now()}] Established connection with payload on {host}:{port}.")
        
        super().__init__(hb_grace_period,hb_timeout,hb_max_retries,interrupt_on,logging_enabled)
        
        self.obs_func = func
        self.obs_args = args

        # This handles the actual 'observer/payload' code that exists.
        self.obsThread = threading.Thread(target=self.obs_func, args=self.obs_args)
        self.obsThread.start()

        while True: time.sleep(1)

    def trigger(self, signum, frame):
        # We don't actually send any messages to the payload here.
        # This is because by closing the TCP socket, one of a few outcomes occur, all of which result in a trigger state:
        # 1. Payload: TCP PSH ->
        #    Observer: <- TCP RST
        #    Payload has an exception; caught by checkSkt, resulting in SIGTERM.
        # 2. Observer due to send heartbeat (TCP PSH), but does not exist.
        #    Payload inevitably times out on its receive and results in SIGTERM.
        # 3. Observer actually still has connection, and sends TCP RST (closing it entirely).
        print(f"[{self}][{__name__}][{datetime.datetime.now()}] Observer triggered. Severing lifeline.")
        self.lifelineSkt.close()
        # We /were/ trying to use TCP KEEPALIVE packets here, but handling of what this means when a process is killed unceremoniously was unstable.
        # On windows hosts, the socket was kept alive via KEEPALIVE packets after the process was killed.
        # We assume most KEEPALIVE implementations assume kernel-level control over what is defined as a 'dead socket', since KEEPALIVE works at the transport layer (not the application) due to it being implemented in TCP.
        # Therefore it stands to reason that it would be nonsensical for KEEPALIVE to *not* represent whether the link is alive at the kernel-level and below...
        sys.exit(signum)

class plProcess(DMSProcess):

    def __init__(self,host,port,hb_grace_period=5.0,hb_timeout=5.0,hb_max_retries=5,hs_timeout=5.0,interrupt_on=(),logging_enabled=False,func=False,args=()):
        assert func, f"[{self}][{__name__}][{datetime.datetime.now()}] No payload function provided!"
        
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host,port))
                s.settimeout(hs_timeout)
                s.listen()
                self.lifelineSkt, addr = s.accept()
            except Exception as e:
                print(f"[{self}][{__name__}][{datetime.datetime.now()}] Failed to establish connection with observer on {host}:{port}.")
                print(f"[{self}][{__name__}][{datetime.datetime.now()}] Exception: {e}.")
                sys.exit(404)
            
            print(f"[{self}][{__name__}][{datetime.datetime.now()}] Established connection with observer on {host}:{port}.")

        super().__init__(hb_grace_period,hb_timeout,hb_max_retries,interrupt_on,logging_enabled)

        # We don't actually *have* a separate thread for the payload to run, as we don't need to handle it all individually.
        self.pl_func = func
        self.pl_args = args
        
        while True: time.sleep(1)

    def trigger(self, signum, frame):
        print(f"[{self}][self.trigger][{datetime.datetime.now()}] - Payload Triggered!")
        try: 
            print(f"[{self}][self.trigger][{datetime.datetime.now()}] - Closing socket to alert Observer!")
            self.lifelineSkt.close()
        except: print(f"[{self}][self.trigger][{datetime.datetime.now()}] - Failed to close socket on trigger!")
        try:
            print(f"[{self}][self.trigger][{datetime.datetime.now()}] - Attempting execution of payload.")
            self.pl_func(self.pl_args)
            print(f"[{self}][self.trigger][{datetime.datetime.now()}] - Payload finished without apparent exception.")
        except:
            print(f"[{self}][self.trigger][{datetime.datetime.now()}] - Payload execution failed! Traceback:")
            traceback.print_exc()
        finally: 
            print(f"[{self}][self.trigger][{datetime.datetime.now()}] - terminated with signal {signum}.")
            sys.exit(signum)