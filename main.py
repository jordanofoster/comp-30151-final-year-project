import subprocess
import logging
import argparse
import sys, signal, time, threading, datetime, traceback, os
from dms import logfmt, datefmt

def _log_level(l):
    match l:
        case "DEBUG": return logging.DEBUG
        case "INFO": return logging.INFO
        case "WARNING": return logging.WARNING
        case "ERROR": return logging.ERROR
        case "CRITICAL": return logging.CRITICAL
        case _: raise argparse.ArgumentTypeError(f"Invalid log level provided ('{l}')")

def _check_dir(p):
    if not os.path.isdir(p):
        raise argparse.ArgumentTypeError("non-directory argument provided.")
    else: return p

parser = argparse.ArgumentParser(
    prog='DMS',
    description='Dead Man\'s Switch utility written in Python'
)

parser.add_argument('-p', '--payload', action="store", nargs="+", type=str)
parser.add_argument('-o', '--observer', action="store", nargs="+", type=str)
parser.add_argument('--timeout', type=float, default=0)
parser.add_argument('--retries', type=int, default=0)
parser.add_argument('--headless', action="store_true")
parser.add_argument('--env', action="store", type=_check_dir)
parser.add_argument('--log-level', choices=[logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL], default="INFO", type=_log_level)
parser.add_argument('--log-file', action='store')
    
args = parser.parse_args()

if args.log_file: logging.basicConfig(filename=args.log_file, level=args.log_level, format=logfmt, datefmt=datefmt)
elif args.log_level < logging.WARNING: logging.basicConfig(stream=sys.stdout, level=args.log_level, format=logfmt, datefmt=datefmt)
else: logging.basicConfig(stream=sys.stderr, level=args.log_level, format=logfmt, datefmt=datefmt)

def checkLiveness(sp):
    logger = logging.getLogger(__file__).getChild(__name__)

    while True:
        if sp.poll():
            logger.critical(f"Received returncode {sp.returncode} from {sp.pid}")
            return sp.returncode
        else:
            logger.debug(f"{sp} appears to be alive.")

def main(args):
    logger = logging.getLogger(__file__).getChild(__name__)

    if args.headless: 
        logger.info("--headless option passed; will run independently.")
        creationflags = subprocess.DETACHED_PROCESS
    else: creationflags = 0

    lifelineEstablished = False
    if args.headless and (sys.platform == 'win32'):
        logger.debug('windows platform detected; using pythonw.')
        pythonExec = 'pythonw'
    else:
        logger.debug('non-windows platform detetcted; using python.')
        pythonExec = 'python'

    if args.env:
        execArgs = [args.env+pythonExec]
    else: execArgs = [pythonExec]
    logging.info(f"executable to be used to call DMS: {str(execArgs)}")
    while not lifelineEstablished and args.retries > -1:
        logging.warning(f"Retries left: {args.retries}")
        args.retries -= 1
        if args.payload:
            logging.info(f"Launching payload subprocess...")
            plP = subprocess.Popen(execArgs+args.payload[0].split(" "), creationflags=creationflags)
            logging.info("Payload subprocess launched.")
        else: 
            logging.info("--payload flag not provided; assuming remote payload.")
            plP = False
        if args.observer:
            logging.info(f"Launching observer subprocess...")
            obsP = subprocess.Popen(execArgs+args.observer[0].split(" "), creationflags=creationflags)
            logging.info("Observer subprocess launched.")
        else: 
            logging.info("--observer flag not provided; assuming remote observer.")
            obsP = False
        try:
            if args.timeout == 0:
                logging.warn(f"No timeout has been provided; will be unable to tell if DMS has launched successfully.")
            if plP: 
                logging.info("Waiting to see if payload fails handshake...")
                plP.wait(timeout=args.timeout)
                logging.error("Payload failed handshake.")
            if obsP: 
                logging.info("Waiting to see if observer fails handshake...")
                obsP.wait(timeout=args.timeout)
                logging.error("Observer appears to have failed handshake.")
        except subprocess.TimeoutExpired:
            # Handshake must have succeeded
            lifelineEstablished = True
            logging.info(f"Handshake assumed successful - no process failure for {args.timeout} second(s)")
        except:
            logging.debug(traceback.print_exc())
        finally:
            if not lifelineEstablished:
                logging.error(f"Lifeline handshake failed.")
                if plP: plP.kill()
                if obsP: obsP.kill()
            else:
                logging.info("Finished lifeline check.")
                break
    
    if not lifelineEstablished:
        logging.critical("lifeline was not established within allocated retries. Exiting.")
        sys.exit(1)

    # At this point our handshake has been established.
    if args.headless:
        logging.info(f"--headless flag has been passed, so not keeping logs; terminating.")
        sys.exit(0)
    else:
        logging.info("Checking to see if payload and obsever remains alive...")
        if plP: threading.Thread(target=checkLiveness, args=(plP,)).start()
        if obsP: threading.Thread(target=checkLiveness, args=(obsP,)).start()
        
        while True: 
            try:
                time.sleep(1)
            except KeyboardInterrupt:
                logging.critical(f"Explicit keyboard interrupt received. Killing observer and payload.")
                if plP: plP.kill()
                if obsP: obsP.kill()
                sys.exit(signal.SIGINT)
            except Exception as e:
                logger.debug(traceback.print_exc())
                sys.exit(signal.SIGTERM)
                

if __name__ == "__main__":
    logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.INFO)

    main(args)
