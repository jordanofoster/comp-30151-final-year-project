import dms
import tkinter as tk
from tkinter import simpledialog
import argparse, logging
import signal
import sys

def _log_level(l):
    match l:
        case "DEBUG": return logging.DEBUG
        case "INFO": return logging.INFO
        case "WARNING": return logging.WARNING
        case "ERROR": return logging.ERROR
        case "CRITICAL": return logging.CRITICAL
        case _: raise argparse.ArgumentTypeError(f"Invalid log level provided ('{l}')")

parser = argparse.ArgumentParser(
    prog='facial recognition component for DMS',
    description='Facial recognition observer component for DMS.'
)
        
parser.add_argument('host', action='store', type=str)
parser.add_argument('port', action='store', type=int)
parser.add_argument('--heartbeat-grace-period', action='store', type=float)
parser.add_argument('--heartbeat-max-retries', action='store', type=int)
parser.add_argument('--heartbeat-timeout', action='store', type=float)
parser.add_argument('--handshake-timeout', action='store', type=float)
parser.add_argument('--log-level', action='store', type=_log_level)
parser.add_argument('--log-file', action='store')

args = parser.parse_args()

if args.log_file: logging.basicConfig(filename=args.log_file, level=args.log_level)
elif args.log_level < logging.WARNING: logging.basicConfig(stream=sys.stdout, level=args.log_level, format=dms.logfmt, datefmt=dms.datefmt)
else: logging.basicConfig(stream=sys.stderr, level=args.log_level, format=dms.logfmt, datefmt=dms.datefmt)

TRIGGER_DELAY = 10000
TRIGGER_MSG=f"""The DMS appears to have been triggered, as the lifeline has been severed.

The payload will trigger in {TRIGGER_DELAY/1000} second(s).

Click the button below to defuse the payload. Note that the DMS will shutdown regardless, and will require manual setup."""

def plFunction(args):
    logger = logging.getLogger(__file__).getChild(__name__)

    top = tk.TopLevel()
    top.title = "DMS Triggered"
    tk.Message(top, text=TRIGGER_MSG, padx=20, pady=20).pack()
    top.after(TRIGGER_DELAY, top.destroy)

    #root = tkinter.Tk()
    #tkinter.Button(root, text="DEFUSE", command=sys.exit(signal.SIGINT)).pack()

    #root.mainloop()
    
    # Payload is as follows
    print(f"{args}: ARGH!")

if __name__ == "__main__":
    logger = logging.getLogger(__file__).getChild(__name__)
    
    logging.info('starting payload process...')
    dms.plProcess(
        args.host,
        args.port,
        func=plFunction,
        args=(args))
    logging.info('payload process started.')