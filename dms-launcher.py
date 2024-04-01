import subprocess
import logging
import argparse
import sys, signal, time, threading, datetime, traceback, os
import dms


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

if args.log_file: logging.basicConfig(filename=args.log_file, level=args.log_level, format=dms.logfmt, datefmt=dms.datefmt)
elif args.log_level < logging.WARNING: logging.basicConfig(stream=sys.stdout, level=args.log_level, format=dms.logfmt, datefmt=dms.datefmt)
else: logging.basicConfig(stream=sys.stderr, level=args.log_level, format=dms.logfmt, datefmt=dms.datefmt)

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
    logger.info(f"executable to be used to call DMS: {str(execArgs)}")
    while not lifelineEstablished and args.retries > -1:
        args.retries -= 1
        if args.payload:
            logger.info(f"Launching payload subprocess...")
            plP = subprocess.Popen(execArgs+args.payload[0].split(" "), creationflags=creationflags)
            logger.info("Payload subprocess launched.")
        else: 
            logger.info("--payload flag not provided; assuming remote payload.")
            plP = False
        if args.observer:
            logger.info(f"Launching observer subprocess...")
            obsP = subprocess.Popen(execArgs+args.observer[0].split(" "), creationflags=creationflags)
            logger.info("Observer subprocess launched.")
        else: 
            logger.info("--observer flag not provided; assuming remote observer.")
            obsP = False
        try:
            if args.timeout == 0:
                logger.warning(f"No timeout has been provided; will be unable to tell if DMS has launched successfully.")
            if plP: 
                logger.info("Waiting to see if payload fails handshake...")
                plP.wait(timeout=args.timeout)
                logger.error("Payload failed handshake.")
            if obsP: 
                logger.info("Waiting to see if observer fails handshake...")
                obsP.wait(timeout=args.timeout)
                logger.error("Observer appears to have failed handshake.")
            
            logger.warning(f"Retries left: {args.retries}")
        except subprocess.TimeoutExpired:
            # Handshake must have succeeded
            lifelineEstablished = True
            logger.info(f"Handshake assumed successful - no process failure for {args.timeout} second(s)")
        except:
            logger.debug(traceback.print_exc())
        finally:
            if not lifelineEstablished:
                logger.error(f"Lifeline handshake failed.")
                if plP: plP.kill()
                if obsP: obsP.kill()
            else:
                logger.info("Finished lifeline check.")
                break
    
    if not lifelineEstablished:
        logger.critical("lifeline was not established within allocated retries. Exiting.")
        sys.exit(1)

    # At this point our handshake has been established.
    if args.headless:
        logger.info(f"--headless flag has been passed, so not keeping logs; terminating.")
        sys.exit(0)
    else:
        logger.info(f"plP: {bool(plP)}")
        logger.info(f"obsP: {bool(obsP)}")
        try:
            logger.info("Awaiting")
            if obsP: obsP.wait()
            if plP: plP.wait()
            raise dms.launcherFinishedException(0)
        except KeyboardInterrupt:
            logger.critical(f"Explicit keyboard interrupt received. Killing all subprocesses.")
            if obsP: 
                obsP.kill()
                obsP.wait()
            if plP:
                plP.kill()
                plP.wait()
            raise dms.launcherFinishedException(signal.SIGINT)
        except BaseException as e:
            logger.debug(traceback.print_exc())
            raise dms.launcherFinishedException(signal.SIGTERM)


                

if __name__ == "__main__":
    logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.INFO)

    main(args)
