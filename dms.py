from abc import ABC, abstractmethod
import socket, threading, signal, datetime, time, traceback, sys, logging

logfmt=(f"[PID-%(process)d][TID-%(thread)d][%(module)s][%(funcName)s][%(asctime)s][%(levelname)s]: %(message)s")
datefmt="%Y-%m-%d %H:%M:%S"

class observerTriggerException(Exception):
    "Raised when the observer process intentionally fires a trigger event."
    pass

class observerInitException(SystemExit):
    "Raised when observer thread fails to initialise."
    pass

class lifelineInitException(SystemExit):
    "Raised when lifeline thread fails to initialise."
    pass

class lifelineSeveredException(Exception):
    "Raised when the lifeline thread is intentionally severed by the observer or payload processes."
    pass

class lifelineDeadException(Exception):
    "Raised when the observer or payload processes fails to receive a heartbeat after a set number of retries."
    pass

class tamperingEventException(Exception):
    "Raised when tamper evident events _unrelated to the trigger condition_ occur outside of the process scope such as SIGTERM, the removal of files, or likewise."
    pass

class payloadExecutionException(Exception):
    "Raised when a triggered payload process fails to execute due to known exceptions."
    pass

class payloadOutcomeException(Exception):
    "Raised when a triggered payload executes without exception, but cannot verify the intended outcome at the endpoint level."
    pass

class triggerFinishedException(Exception):
    "Raised when the trigger procedure for a thread concludes, regardless of type or outcome."
    pass

class DMSProcess(ABC):

    def __init__(self,hb_grace_period=5.0,hb_timeout=5.0,hb_max_retries=0,interrupt_on=()):
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
        self.lifelineThread = threading.Thread(target=self.checkSkt, daemon=True)
        self.lifelineThread.start()

    def checkSkt(self):
        logger = self.classLogger.getChild(__name__)

        while True:
            logger.info(f"Entering heartbeat grace period for {self.HEARTBEAT_GRACE_PERIOD} second(s).")
            time.sleep(self.HEARTBEAT_GRACE_PERIOD)
            try:
                self.lifelineSkt.send(b'1')
                logger.info("Heartbeat sent.")
                # 'pingpong' heartbeat: Observer has to send heartbeat and expects one back from payload.
                logger.info("Awaiting heartbeat...")
                self.lifelineSkt.recv(1)
                logger.info("Heartbeat received.")
                # TODO: Do any race conditions occur here?
                self.HEARTBEAT_ATTEMPTS_FAILED = 0
            except:
                logger.warn("Failed to receive heartbeat.")
                if self.HEARTBEAT_ATTEMPTS_FAILED != self.HEARTBEAT_MAXIMUM_RETRIES:
                    self.HEARTBEAT_ATTEMPTS_FAILED += 1
                    logger.warn(f"Retries left: {self.HEARTBEAT_MAXIMUM_RETRIES-self.HEARTBEAT_ATTEMPTS_FAILED} [{self.HEARTBEAT_ATTEMPTS_FAILED}/{self.HEARTBEAT_MAXIMUM_RETRIES}].")
                    pass
                else:
                    logger.critical("Maximum retries exceeded. Closing socket and sending SIGTERM.")
                    self.lifelineSkt.close()
                    raise lifelineDeadException(signal.SIGTERM)
    
    @abstractmethod
    def trigger():
        pass


class obsProcess(DMSProcess):
    
    def __init__(self,host,port,hb_grace_period=5.0,hb_timeout=5.0,hb_max_retries=5,hs_timeout=5.0,interrupt_on=(),func=False,args=()):

        self.classLogger = logging.getLogger(__file__).getChild(str(self))

        logger = self.classLogger.getChild(__name__)
        
        assert func, logger.error("No observer function provided!")
        try:
            self.lifelineSkt = socket.create_connection((host,port),timeout=hs_timeout)
        except Exception as e:
            logger.critical(f"Failed to initiate connection with payload on {host}:{port}.")
            logger.error(f"Exception: {e}.")
            raise lifelineInitException(404)
        
        logger.info(f"Established connection with payload on {host}:{port}.")
        
        super().__init__(hb_grace_period,hb_timeout,hb_max_retries,interrupt_on)
        
        self.obs_func = func
        self.obs_args = args

        # This handles the actual 'observer/payload' code that exists.
        self.obsThread = threading.Thread(target=self.obs_func, args=self.obs_args)
        self.obsThread.start()

        while True:
            obsRet = self.obsThread.join(timeout=1)
            if obsRet is not None:
                print(obsRet)
                self.trigger()
            else:
                if self.obsThread.is_alive(): pass
                else: 
                    logger.warn("obsThread appeared to exit normally, which shouldn't happen.")
                    raise Exception

            lifelineRet = self.lifelineThread.join(timeout=1)
            if lifelineRet is not None:
                print(lifelineRet)
                self.trigger()
            else:
                if self.lifelineThread.is_alive(): pass
                else: 
                    logger.warn("lifelineThread appeared to exit normally, which shouldn't happen.")
                    raise Exception

    def trigger(self, signum, frame):
        logger = self.classLogger.getChild(__name__)
        # We don't actually send any messages to the payload here.
        # This is because by closing the TCP socket, one of a few outcomes occur, all of which result in a trigger state:
        # 1. Payload: TCP PSH ->
        #    Observer: <- TCP RST
        #    Payload has an exception; caught by checkSkt, resulting in SIGTERM.
        # 2. Observer due to send heartbeat (TCP PSH), but does not exist.
        #    Payload inevitably times out on its receive and results in SIGTERM.
        # 3. Observer actually still has connection, and sends TCP RST (closing it entirely).
        logger.critical("Observer triggered. Attempting to sever lifeline.")
        self.lifelineSkt.close()
        logger.debug("Lifeline severed.")
        # We /were/ trying to use TCP KEEPALIVE packets here, but handling of what this means when a process is killed unceremoniously was unstable.
        # On windows hosts, the socket was kept alive via KEEPALIVE packets after the process was killed.
        # We assume most KEEPALIVE implementations assume kernel-level control over what is defined as a 'dead socket', since KEEPALIVE works at the transport layer (not the application) due to it being implemented in TCP.
        # Therefore it stands to reason that it would be nonsensical for KEEPALIVE to *not* represent whether the link is alive at the kernel-level and below...
        raise triggerFinishedException(signum)

class plProcess(DMSProcess):

    def __init__(self,host,port,hb_grace_period=5.0,hb_timeout=5.0,hb_max_retries=5,hs_timeout=5.0,interrupt_on=(),func=False,args=()):

        self.classLogger = logging.getLogger(__file__).getChild(str(self))

        logger = self.classLogger.getChild(__name__)

        assert func, logger.error("No payload function provided!")
        
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host,port))
                s.settimeout(hs_timeout)
                s.listen()
                self.lifelineSkt, addr = s.accept()
            except Exception as e:
                logger.critical(f"Failed to establish connection with observer on {host}:{port}.")
                logger.error(f"Exception: {e}.")
                raise lifelineInitException(404)
            
            logger.info(f"Established connection with observer on {host}:{port}.")

        super().__init__(hb_grace_period,hb_timeout,hb_max_retries,interrupt_on)

        # We don't actually *have* a separate thread for the payload to run, as we don't need to handle it all individually.
        self.pl_func = func
        self.pl_args = args
        
        while True:

            lifelineRet = self.lifelineThread.join(timeout=1)
            if lifelineRet is not None:
                print(lifelineRet)
                self.trigger()
            else:
                if self.lifelineThread.is_alive(): pass
                else: 
                    logger.warn("lifelineThread appeared to exit normally, which shouldn't happen.")
                    raise Exception

    def trigger(self, signum, frame):
        logger = self.classLogger.getChild(__name__)

        logger.critical("Payload Triggered!")
        try: 
            logger.critical("Attempting to sever lifeline to alert Observer!")
            self.lifelineSkt.close()
            raise lifelineSeveredException
        except lifelineSeveredException: logger.critical("Lifeline severed!")
        except Exception as e: 
            logging.error(f"Failed to sever lifeline - {e}! Traceback:")
            traceback.print_exc()
        try:
            logging.info("Attempting execution of payload.")
            self.pl_func(self.pl_args)
            logging.info("Payload finished, with outcome verified.")
        except payloadExecutionException:
            logging.critical("Payload execution failed! Traceback:")
            traceback.print_exc()
        except payloadOutcomeException:
            logging.warn("Payload executed without error, but could not verify outcome. Traceback:")
            traceback.print_exc()
        except Exception as e:
            logging.error(f"Unexpected exception occurred - {e}. Traceback:")
            traceback.print_exc()
        finally: 
            logging.critical(f"terminating with signal {signum}.")
            triggerFinishedException(signum)