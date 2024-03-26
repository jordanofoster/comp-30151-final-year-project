from dms import plProcess
import argparse

def plFunction(args):
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