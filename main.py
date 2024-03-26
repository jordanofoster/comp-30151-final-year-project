import subprocess
import logging
import argparse
import sys, signal, time, threading, datetime, traceback
def checkLiveness(sp):
    while True:
        if sp.poll():
            print(f"[{__name__}][{datetime.datetime.now()}] Received returncode {sp.returncode} from {sp.pid}")
            signal.raise_signal(sp.returncode)

def main(args):
    if args.headless: 
        print(f"[Initiator][{__name__}][{datetime.datetime.now()}] --headless option passed; will run independently.")
        creationflags = subprocess.DETACHED_PROCESS
    else: creationflags = 0

    lifelineEstablished = False

    while not lifelineEstablished:
        if args.payload:
            print(f"[Initiator][{__name__}][{datetime.datetime.now()}] Starting payload subprocess...")
            plP = subprocess.Popen(["python"]+args.payload[0].split(" "), creationflags=creationflags)
        else: plP = False
        if args.observer:
            print(f"[Initiator][{__name__}][{datetime.datetime.now()}] Starting observer subprocess...")
            obsP = subprocess.Popen(["python"]+args.observer[0].split(" "), creationflags=creationflags)
        else: obsP = False
        try:
            if args.timeout is None:
                print(f"[Initiator][{__name__}][{datetime.datetime.now()}] No timeout provided. Will assume 0s (not checking).")
                args.timeout = 0
            if plP: plP.wait(timeout=args.timeout)
            if obsP: obsP.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            # Handshake must have succeeded
            lifelineEstablished = True
            print(f"[Initiator][{__name__}][{datetime.datetime.now()}] Handshake assumed successful - no process failure for {args.timeout} second(s)")
        except:
            print(traceback.print_exc())
        finally:
            if not lifelineEstablished:
                print(f"[Initiator][{__name__}][{datetime.datetime.now()}] Lifeline handshake failed. Retrying...")
                if plP: plP.kill()
                if obsP: obsP.kill()
            else:
                break
    
    # At this point our handshake has been established.
    if args.headless:
        print(f"[Initiator][{__name__}][{datetime.datetime.now()}] Headless; terminating.")
        sys.exit(0)
    else:
        if plP: threading.Thread(target=checkLiveness(plP)).start()
        if obsP: threading.Thread(target=checkLiveness(obsP)).start()
        
        while True: 
            try:
                time.sleep(1)
            except KeyboardInterrupt:
                if plP: plP.kill()
                if obsP: obsP.kill()
                

if __name__ == "__main__":
    logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.INFO)

    parser = argparse.ArgumentParser(
        prog='DMS',
        description='Dead Man\'s Switch utility written in Python'
    )

    parser.add_argument('-p', '--payload', action="store", nargs="+", type=str)
    parser.add_argument('-o', '--observer', action="store", nargs="+", type=str)
    parser.add_argument('--timeout', type=float)
    parser.add_argument('--headless', action="store_true")
    
    args = parser.parse_args()
    main(args)
