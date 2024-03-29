from dms import plProcess
import tkinter as tk
from tkinter import simpledialog
import argparse
import signal
import sys

TRIGGER_DELAY = 10000
TRIGGER_MSG=f"""The DMS appears to have been triggered, as the lifeline has been severed.

The payload will trigger in {TRIGGER_DELAY/1000} second(s).

Click the button below to defuse the payload. Note that the DMS will shutdown regardless, and will require manual setup."""

def plFunction(args):
    
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

    args = parser.parse_args()
    
    plProcess(
        args.host,
        args.port,
        func=plFunction,
        args=('arg'))