import dms, argparse, logging, sys, os

def _log_level(l):
    match l:
        case "DEBUG": return logging.DEBUG
        case "INFO": return logging.INFO
        case "WARNING": return logging.WARNING
        case "ERROR": return logging.ERROR
        case "CRITICAL": return logging.CRITICAL
        case _: raise argparse.ArgumentTypeError(f"Invalid log level provided ('{l}')")

def _secret_exists(s):
    if os.path.exists(s): return s
    else: raise argparse.ArgumentTypeError("All secrets must be valid files, directories or symbolic link paths.")

parser = argparse.ArgumentParser(
    prog='File erasure payload for DMS.',
    description='File erasure payload for DMS.'
)
        
parser.add_argument('host', action='store', type=str)
parser.add_argument('port', action='store', type=int)
parser.add_argument('secrets', action='store', nargs='+', type=_secret_exists)
parser.add_argument('--heartbeat-grace-period', action='store', type=float)
parser.add_argument('--heartbeat-max-retries', action='store', type=int)
parser.add_argument('--heartbeat-timeout', action='store', type=float)
parser.add_argument('--handshake-timeout', action='store', type=float)
parser.add_argument('--alert', action='store_true')
parser.add_argument('--defuse', action='store', type=int)
parser.add_argument('--log-level', choices=[logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL], default="INFO", type=_log_level)
parser.add_argument('--log-file', action='store')

args = parser.parse_args()

if args.log_file: logging.basicConfig(filename=args.log_file, level=args.log_level, format=dms.logfmt, datefmt=dms.datefmt)
elif args.log_level < logging.WARNING: logging.basicConfig(stream=sys.stdout, level=args.log_level, format=dms.logfmt, datefmt=dms.datefmt)
else: logging.basicConfig(stream=sys.stderr, level=args.log_level, format=dms.logfmt, datefmt=dms.datefmt)

if args.alert:
    if not os.path.exists('.env'): raise argparse.ArgumentTypeError('--alert: .env does not appear to exist, and is required for e-mail alerts to work.')
    from dotenv import load_dotenv
    try: load_dotenv()
    except: raise argparse.ArgumentTypeError('--alert: .env does not appear to follow the expected format for an environment file.')
    if 'SMTP_HOST' not in os.environ: raise argparse.ArgumentTypeError('--alert: .env appears to be missing a required environment variable (SMTP_HOST).')
    elif 'SMTP_FROM' not in os.environ: raise argparse.ArgumentTypeError('--alert: .env appears to be missing a required environment variable (SMTP_FROM).')
    elif 'SMTP_PASS' not in os.environ: raise argparse.ArgumentTypeError('--alert: .env appears to be missing a required environment variable (SMTP_PASS).')
    elif 'SMTP_TO' not in os.environ: raise argparse.ArgumentTypeError('--alert: .env appears to be missing a required environment variable (SMTP_TO).')

if args.defuse:
    TRIGGER_DELAY = args.defuse*1000
    TRIGGER_MSG=f"""
    The DMS appears to have been triggered, as the lifeline has been severed.

    The payload will trigger in {args.defuse} second(s).

    Click the button below to defuse the payload. Note that the DMS will shutdown regardless, and will require manual setup."""

def plFunction(args):
    logger = logging.getLogger(__file__).getChild(__name__)
    
    if args.defuse:
        import tkinter as tk
        root = tk.Tk()
        root.title = 'Payload Triggered'
        root.resizable(False, False)
    
    def defuse():
        logger = logging.getLogger(__file__).getChild(__name__)
        logger.critical("Payload defused by user!")
        root.destroy()
        raise dms.payloadDefusedException

    def verify():
        logger = logging.getLogger(__file__).getChild(__name__)

        verified = True
        logger.info('Verifying that secrets have been deleted...')
        for entry in args.secrets:
            logger.debug(f"Checking {entry}...")
            if os.path.exists(entry):
                logger.critical(f"Secret still exists: {entry}")
                verified = False
            else:
                logger.info(f"Verified that {entry} has been deleted.")

        if 'PGP_PUBKEY' in os.environ:
            logger.info("Verifying that public encryption key has been deleted...")
            if os.path.exists(os.getenv('PGP_PUBKEY')): 
                logger.critical(f"Public encryption key {os.getenv('PGP_PUBKEY')} still exists!")
                verified = False
            else:
                logger.info("Verified that public encryption key has been deleted.")
        if 'PGP_PRIVKEY' in os.environ:
            logger.info("Verifying that private signing key has been deleted...")
            if os.path.exists(os.getenv('PGP_PRIVKEY')): 
                logger.critical(f"Private signing key {os.getenv('PGP_PRIVKEY')} still exists!")
                verified = False
            else:
                logger.info("Verified that private signing key has been deleted.")
        
        logger.info("Verifying that .env file has been deleted...")
        if os.path.exists('.env'):
            logger.critical(f".env file still exists!") 
            verified = False
        else:
            logger.info("Verified that .env file has been deleted.")

        if verified: 
            logger.info("Verified that payload executed successfully.")
            return True
        elif not verified: 
            logger.critical("One or more secrets remain undeleted!")
            raise dms.payloadVerificationException("One or more secrets still appeared to exist after deletion")

    def fire():
        logger = logging.getLogger(__file__).getChild(__name__)

        from shutil import rmtree

        # Payload is as follows
        for entry in args.secrets:
            try:
                if os.path.isfile(entry): os.remove(entry)
                elif os.path.isdir(entry): rmtree(entry)
                elif os.path.islink(entry): os.unlink(entry)
                logger.info(f"Secret deleted: {entry}")
            except:
                logger.critical(f"Secret not deletable: {entry}")
                raise dms.payloadExecutionException

        if args.alert:
            from dotenv import load_dotenv
            import smtplib, ssl, datetime
            from email.message import EmailMessage

            logger.debug("Attempting to load .env...")
            load_dotenv(); 
            logger.debug(".env loaded.")
            logger.debug("Attempting to delete .env...")
            os.remove('.env')
            logger.debug(".env deleted.")

            if os.getenv('PGP_PUBKEY'): import pgpy

            smtp_host=os.getenv('SMTP_HOST')
            smtp_port=os.getenv('SMTP_PORT')
            smtp_from=os.getenv('SMTP_FROM')
            smtp_pass=os.getenv('SMTP_PASS')
            smtp_to=os.getenv('SMTP_TO')

            context = ssl.create_default_context()

            msg = EmailMessage()
            msg['Subject'] = f"DMS Triggered ({datetime.datetime.now()})"
            msg['From'] = smtp_from
            msg['To'] = smtp_to

            msgContents = f"""
            Hi Bob,

            If you're reading this, my DMS set off successfully.
            I won't have access to any accounts I've been using on the computer, so our typical communications will no
            longer work, besides this e-mail. If you receive anything else from this e-mail you should assume it's no longer me.

            We can discuss future plans sometime at [ADDRESS].

            Thanks,

            Alice
            """

            if ('PGP_PUBKEY' or 'PGP_PRIVKEY') in os.environ:
                logger.info("PGP key provided. Will be signing and/or encrypting this message.")
                msgContents = pgpy.PGPMessage.new(msgContents)
                
                # GPG does sign-then-encrypt rather than encrypt-then-sign.
                if 'PGP_PRIVKEY' in os.environ:
                    logger.info("Private signing key provided. Signing this message.")
                    privkey, _ = pgpy.PGPKey.from_file(os.getenv('PGP_PRIVKEY'))

                    msgContents |= privkey.sign(msgContents)
                    logger.info("Messsage signed.")

                    logger.info("Deleting signing key to prevent its reuse...")
                    os.remove(os.getenv('PGP_PRIVKEY'))
                    logger.info("Signing key deleted.")
                
                if 'PGP_PUBKEY' in os.environ:
                    logger.info("Public encryption key of recipient provided. Encrypting this message.")
                    pubkey, _ = pgpy.PGPKey.from_file(os.getenv('PGP_PUBKEY'))

                    msgContents = pubkey.encrypt(msgContents)
                    logger.info("Message encrypted.")

                    logger.info("Deleting encryption key to prevent its reuse...")
                    os.remove(os.getenv('PGP_PUBKEY'))
                    logger.info("Encryption key deleted.")
            else:
                logger.warn("No PGP keys provided. This alert will be unsigned and unencrypted.")

            msg.set_content(str(msgContents))

            with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
                logger.info(f"Attempting SSL connection with {smtp_host}:{smtp_port} as {smtp_from}...")
                server.login(smtp_from, smtp_pass)
                logger.debug("Login successful.")
                logger.debug("Sending message...")
                server.send_message(msg)
                logger.info("Message sent.")
                server.quit()
                logger.info("SMTP SSL connection closed.")

        if verify(): raise dms.triggerFinishedException
        else: raise dms.payloadVerificationException

    if args.defuse:
        logger.info("--defuse argument provided. Spawning defusal GUI.")
        from tkinter import ttk
        ttk.Label(root, text=TRIGGER_MSG).pack()
        ttk.Button(root, text="Defuse", command=lambda:defuse()).pack()
        root.after(TRIGGER_DELAY, lambda:fire())
        root.mainloop()
    else: fire()

if __name__ == "__main__":
    import os
    import dms
    import argparse, logging
    import sys

    logger = logging.getLogger(__file__).getChild(__name__)

    dms.plProcess(
        args.host,
        args.port,
        hb_grace_period=args.heartbeat_grace_period,
        hb_max_retries=args.heartbeat_max_retries,
        hb_timeout=args.heartbeat_timeout,
        hs_timeout=args.handshake_timeout,
        func=plFunction,
        args=(args)
    )
