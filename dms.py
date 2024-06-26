from abc import ABC, abstractmethod
import socket, signal, time, traceback, logging, multiprocessing

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
    "Raised when the observer or payload processes fail to receive a heartbeat after a set number of retries."
    pass

class tamperingEventException(Exception):
    "Raised when tamper evident events _unrelated to the trigger condition_ occur outside of the process scope such as SIGTERM, the removal of files, or likewise."
    pass

class payloadExecutionException(SystemExit):
    "Raised when a triggered payload process fails to execute."
    pass

class payloadVerificationException(SystemExit):
    "Raised when a triggered payload executes without exception, but cannot verify the intended outcome has occurred."
    pass

class payloadDefusedException(SystemExit):
    "Raised when a triggered payload is defused by manual user intervention. Must be supported by the payload function itself."
    pass

class triggerFinishedException(SystemExit):
    "Raised when the trigger procedure for a thread concludes, regardless of type or outcome."
    pass

class launcherFinishedException(SystemExit):
    "Raised by a non-headless DMS launcher when it believes the DMS instance it launched is no longer alive."
    pass

class DMSProcess(ABC):

    def __init__(self,hb_grace_period=5.0,hb_timeout=5.0,hb_max_retries=0):
        baseLogger = self.classLogger.getChild(__name__)
        
        # We use *one* socket for the heartbeat; this is passed to both threads (checking-side and acknowledgement side).

        baseLogger.debug(f"Attempting to set lifelineSkt timeout to {hb_timeout}")
        self.lifelineSkt.settimeout(hb_timeout)
        baseLogger.debug(f"succesfully set timeout of lifelineSKt to {hb_timeout}")

        self.HEARTBEAT_MAXIMUM_RETRIES = hb_max_retries
        self.HEARTBEAT_ATTEMPTS_FAILED = 0
        self.HEARTBEAT_GRACE_PERIOD=hb_grace_period

        # This thread handles our 'heartbeat' signal, separate from the function code.
        baseLogger.debug("Starting lifelineThread...")
        self.lifelineProcess = multiprocessing.Process(target=self.checkSkt, daemon=True)
        self.lifelineProcess.start()
        baseLogger.debug("Lifeline thread started.")

    def checkSkt(self):
        logger = self.classLogger.getChild(__name__)
        while True:
            try:
                logger.info(f"Entering heartbeat grace period for {self.HEARTBEAT_GRACE_PERIOD} second(s).")
                time.sleep(self.HEARTBEAT_GRACE_PERIOD)

                logger.info("Trying to send heartbeat...")
                self.lifelineSkt.sendall(b'1')
                logger.info("Heartbeat sent.")
                # 'pingpong' heartbeat: Observer has to send heartbeat and expects one back from payload.
                logger.info("Awaiting heartbeat...")
                self.lifelineSkt.recv(1)
                logger.info("Heartbeat received.")
                # TODO: Do any race conditions occur here?
                self.HEARTBEAT_ATTEMPTS_FAILED = 0
                continue

            except ConnectionError as e:
                logger.critical('Connection appears to have failed. Severing lifeline.')
                self.lifelineSkt.close()
                raise lifelineDeadException(signal.SIGTERM)
            
            except BlockingIOError as e:
                logger.warning("Failed to send heartbeat immediately, but socket not closed.")
            except TimeoutError as e:
                logger.warning("Failed to receive heartbeat. Retrying...")
            except BaseException as e:
                logger.critical('Unknown exception received. Closing socket.')
                logger.critical(e)
                self.lifelineSkt.close()
                raise lifelineDeadException(e)
                
            if self.HEARTBEAT_ATTEMPTS_FAILED != self.HEARTBEAT_MAXIMUM_RETRIES:
                self.HEARTBEAT_ATTEMPTS_FAILED += 1
                logger.warning(f"Retries left: {self.HEARTBEAT_MAXIMUM_RETRIES-self.HEARTBEAT_ATTEMPTS_FAILED} [{self.HEARTBEAT_ATTEMPTS_FAILED}/{self.HEARTBEAT_MAXIMUM_RETRIES}].")
                continue
            else:
                logger.critical("Maximum retries exceeded. Severing lifeline.")
                self.lifelineSkt.close()
                raise lifelineDeadException(signal.SIGTERM)

    @abstractmethod
    def trigger():
        pass


class obsProcess(DMSProcess):
    
    def __init__(self,host,port,hb_grace_period=5.0,hb_timeout=5.0,hb_max_retries=5,hs_timeout=5.0,func=False,args=()):

        self.classLogger = logging.getLogger(__file__).getChild(str(self))

        logger = self.classLogger.getChild(__name__)
        
        assert func, logger.error("No observer function provided!")
        try:
            logger.info(f"Trying to establish connection with {host} on port {port} with a timeout of {hs_timeout} second(s).")
            self.lifelineSkt = socket.create_connection((host,port),timeout=hs_timeout)
        except BaseException as e:
            logger.critical(f"Failed to initiate connection with payload on {host}:{port}.")
            logger.error(f"Exception: {e}.")
            raise lifelineInitException(404)
        
        logger.info(f"Established connection with payload on {host}:{port}.")
        
        super().__init__(hb_grace_period,hb_timeout,hb_max_retries)
        
        self.obs_func = func
        self.obs_args = args

        # This handles the actual 'observer/payload' code that exists.
        logger.debug("Starting observer thread...")
        self.obsProcess = multiprocessing.Process(target=self.obs_func, args=self.obs_args)
        self.obsProcess.start()
        logger.debug("Observer thread started")

        try:
            while True:
                if self.obsProcess.is_alive() and self.lifelineProcess.is_alive(): pass
                else: break
        except BaseException as e:
            logger.critical('Received exception while monitoring processes. Triggering!')
        finally: self.trigger()

    def trigger(self):
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
        try:
            self.lifelineSkt.close()
            logger.debug('lifelineSkt closed.')
            self.lifelineProcess.terminate()
            logger.debug('Terminated lifelineProcess.')
        except ValueError:
            logger.debug('lifelineProcess already closed.')
        self.lifelineProcess.join()
        self.lifelineProcess.close()
        logger.debug('Released resources held by lifelineProcess.')
        logger.critical("Lifeline severed.")

        logger.info('Attempting to stop obsProcess...')
        try:
            self.obsProcess.terminate()
            logger.debug('Terminated obsProcess.')
        except ValueError:
            logger.debug('obsProcess already closed.')
        logger.debug('Terminated obsProcess.')
        self.obsProcess.join()
        self.obsProcess.close()
        logger.debug('Released resources held by obsProcess.')
        logger.info('obsProcess was stopped successfully.')

        # We /were/ trying to use TCP KEEPALIVE packets here, but handling of what this means when a process is killed unceremoniously was unstable.
        # On windows hosts, the socket was kept alive via KEEPALIVE packets after the process was killed.
        # We assume most KEEPALIVE implementations assume kernel-level control over what is defined as a 'dead socket', since KEEPALIVE works at the transport layer (not the application) due to it being implemented in TCP.
        # Therefore it stands to reason that it would be nonsensical for KEEPALIVE to *not* represent whether the link is alive at the kernel-level and below...
        raise triggerFinishedException(0)

class plProcess(DMSProcess):

    def __init__(self,host,port,hb_grace_period=5.0,hb_timeout=5.0,hb_max_retries=5,hs_timeout=5.0,func=False,args=()):

        self.classLogger = logging.getLogger(__file__).getChild(str(self))

        logger = self.classLogger.getChild(__name__)

        assert func, logger.error("No payload function provided!")
        
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host,port))
                s.settimeout(hs_timeout)
                s.listen()
                self.lifelineSkt, addr = s.accept()
            except BaseException as e:
                logger.critical(f"Failed to establish connection with observer on {host}:{port}.")
                logger.error(f"Exception: {e}.")
                raise lifelineInitException(404)
            
            logger.info(f"Established connection with observer on {host}:{port}.")

        super().__init__(hb_grace_period,hb_timeout,hb_max_retries)

        # We don't actually *have* a separate thread for the payload to run, as we don't need to handle it all individually.
        self.pl_func = func
        self.pl_args = args
        
        try:
            while True:
                if self.lifelineProcess.is_alive(): pass
                else: break
        except BaseException as e:
            logger.critical('Received exception while monitoring processes. Triggering!')
        finally:
            self.trigger()

    def trigger(self):
        logger = self.classLogger.getChild(__name__)

        logger.critical("Payload Triggered!")
        try: 
            logger.critical("Attempting to sever lifeline to alert Observer!")
            self.lifelineSkt.close()
            logger.debug("lifelineSkt closed.")
            self.lifelineProcess.terminate()
            logger.debug("lifelineProcess terminated.")
            raise lifelineSeveredException()
        except lifelineSeveredException: logger.critical("Lifeline severed!")
        except ValueError: logger.debug("lifelineProcess already closed.")
        except BaseException as e: 
            logger.error(f"Failed to sever lifeline - {e}! Traceback:")
            traceback.print_exc()
        
        logger.debug("Checking lifelineProcess has terminated...")
        self.lifelineProcess.join()
        logger.debug("lifelineProcess terminated.")
        self.lifelineProcess.close()
        logger.debug("Released resources held by lifelineProcess.")
        logger.critical("Lifeline severed!")
        
        try:
            logger.info("Attempting execution of payload.")
            self.pl_func(self.pl_args)
            logger.info("Payload finished, with outcome verified.")
        except payloadExecutionException:
            logger.critical("Payload execution failed!")
            logger.debug(traceback.print_exc())
            raise triggerFinishedException(2)
        except payloadVerificationException:
            logger.warning("Payload executed without error, but could not verify outcome. Traceback:")
            logger.debug(traceback.print_exc())
            raise triggerFinishedException(3)
        except payloadDefusedException:
            logger.warning("Payload was manually defused via user input, so did not trigger.")
            raise triggerFinishedException(4)
        except triggerFinishedException:
            logger.info("Payload function signalled that trigger finished successfully.")
            raise triggerFinishedException(0)
        except BaseException as e:
            logger.error(f"Unexpected exception occurred - {e}. Traceback:")
            logger.debug(traceback.print_exc())
            raise triggerFinishedException(1)