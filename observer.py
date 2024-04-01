import dms
import signal, traceback, time, argparse, sys, os, multiprocessing, glob, logging, datetime, queue, threading

VALID_EMOTIONS=(
    "happy",
    "neutral",
    "surprise",
    "sad",
    "angry",
    "fear",
    "disgust"
)

IGNORE_FILES = (
    "ds_vggface_opencv_v2.pkl"
    "ds_vggface_ssd_v2.pkl"
    "ds_ghostfacenet_opencv_v2.pkl"
)

def _emotions(e):
    try:
        emotion, min_score = e.split(':')
        if emotion not in VALID_EMOTIONS: raise Exception
        min_score = float(min_score)
        return emotion, min_score
    except:
        raise argparse.ArgumentTypeError(f"Forbidden emotions must be in format emotion:min_score.\nmin_score must be a valid float between 0-100.\nemotion must be a valid emotion out of the following: happy, neutral, surprise, sad, angry, fear, disgust.")

def _lim_faces(f):
    if int(f) < 0:
        raise argparse.ArgumentTypeError("Face bounds (--min-faces and --max-faces) cannot be less than 0.")
    else: return int(f)

def _face_path(p):
    if os.path.isfile(p) or os.path.isdir(p): return [p]
    else: raise argparse.ArgumentTypeError(f"Identity flags (--known-faces, --require-faces and --reject-faces) must be valid filenames or paths. {p} is an invalid path.")

def _frame_dump_path(p):
    if os.path.isdir(p): return p
    else: raise argparse.ArgumentTypeError(f"a directory must be provided to the --dump-frames argument as a base. {p} is not a directory.")

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

# Trigger when a frame cannot be returned from cv2.VideoCapture.
parser.add_argument('--reject-noframe', action="store_true")

parser.add_argument('--min-faces', default=0, action="store", type=_lim_faces)
parser.add_argument('--max-faces', action="store", type=_lim_faces)

# Enforcing verification of faces necessarily implies rejection of unknown faces
parser.add_argument('--known-faces', action="extend", nargs="+", type=_face_path)
parser.add_argument('--require-faces', action="extend", nargs="+", type=_face_path)
parser.add_argument('--reject-faces', action="extend", nargs="+", type=_face_path)
parser.add_argument('--reject-unknown', action="store_true")

parser.add_argument('--reject-emotions', action="store", nargs="*", type=_emotions)
parser.add_argument('--noblock', action="store_true", default=False)

parser.add_argument('--detector-backend', action="store", default='opencv', choices=["opencv","ssd","dlib","mtcnn","retinaface","mediapipe","yolov8","yunet","fastmtcnn"])
parser.add_argument('--model-name', action="store", default='GhostFaceNet', choices=["VGG-Face","Facenet","Facenet512","OpenFace","DeepFace","DeepID","ArcFace","Dlib","SFace","GhostFaceNet"])

parser.add_argument('--sliding-window-size', action="store", default=10, type=int)
parser.add_argument('--max-buffer-size', action="store", default=0, type=int)

parser.add_argument('--dump-frames', action="store", type=_frame_dump_path)
parser.add_argument('--log-level', choices=[logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL], default="INFO", type=_log_level)
parser.add_argument('--log-file', action="store", default=None)

args = parser.parse_args()

if args.max_faces: assert (args.min_faces < args.max_faces), f"--min-faces ({args.min_faces}) is greater than (or the same as) --max-faces ({args.max_faces})"

# This is some complicated pythonic list comprehension, but we're basically flattening a nested list of 'children' within directories (to allow for both directories and files).
# Doing as such was necessary when we were using glob to find child identities within directories, since we'd get nested lists from the result.
# Now that we treat a 'folder' argument as a single identity with multiple 'training' images it might not be needed, but if it isn't broke...
if args.known_faces: args.known_faces = { file for entry in args.known_faces for file in entry }
if args.require_faces: args.require_faces = { file for entry in args.require_faces for file in entry }
if args.reject_faces: args.reject_faces = { file for entry in args.reject_faces for file in entry }

if (args.require_faces and args.reject_faces):
    for face in args.require_faces: assert (face not in args.reject_faces), f"Identity ({face}) provided in both --auth-faces and --reject-faces arguments"

if not (
    args.reject_noframe or \
    args.min_faces or \
    args.max_faces or \
    args.require_faces or \
    args.reject_faces or \
    args.reject_unknown or \
    args.reject_emotions
): raise argparse.ArgumentTypeError("No trigger conditions have been specified!")

if args.reject_emotions:
    forbidden_emotions = {}
    for emotion,score in args.reject_emotions:
        forbidden_emotions[emotion] = score

if (args.max_buffer_size != 0): # This is the default value for multiprocessing.Manager().Queue, and signifies an 'uncapped' queue size.
    assert (args.sliding_window_size <= args.max_buffer_size), "Sliding window size larger than queue buffer!" 

if args.log_file: logging.basicConfig(filename=args.log_file, level=args.log_level, format=dms.logfmt, datefmt=dms.datefmt)
elif args.log_level < logging.WARNING: logging.basicConfig(stream=sys.stdout, level=args.log_level, format=dms.logfmt, datefmt=dms.datefmt)
else: logging.basicConfig(stream=sys.stderr, level=args.log_level, format=dms.logfmt, datefmt=dms.datefmt)

def getFrameFromWebcam(triggerEvent, frameQueue):
    try:
        logger = logging.getLogger(__file__).getChild(__name__)

        from cv2 import VideoCapture

        video_capture = VideoCapture(0)

        while True:
            if triggerEvent.is_set():
                logger.critical("other thread(s) triggered. Cleaning up.")
                raise dms.observerTriggerException()

            logger.debug("trying to get frame from webcam...")
            ret, frame = video_capture.read()
            logger.debug("got frame from webcam.")
            if not ret:
                logger.warning("Could not get frame from webcam.")
                if args.reject_noframe:
                    logger.critical("TRIGGER: --reject-noframe flag set.")
                    raise dms.observerTriggerException()
                else:
                    pass
            else:
                if args.noblock:
                    try:
                        logger.debug("trying to put frame into frameQueue (--noblock)...")
                        frameQueue.put_nowait(frame)
                        logger.debug("put frame into frameQueue.")
                    except queue.Full:
                        logger.debug("frameQueue is full: --noblock flag set. Moving on.")
                else:
                    logger.debug("waiting to put frame into frameQueue...")
                    frameQueue.put(frame)
                    logger.debug("put frame into framequeue.")
            
    except BaseException as e:
        triggerEvent.set()
        logger.debug(traceback.print_exc())
        raise dms.observerTriggerException()
    
def enumFacesInFrame(triggerEvent, detectorLock, frameQueue, faceDetectionQueue=None):
    try:
        logger = logging.getLogger(__file__).getChild(__name__)

        logger.info("thread started.")
    
        logger.info("loading imports...")
        from deepface.DeepFace import extract_faces
        logger.info("imports loaded.")
    
        while True:
            if triggerEvent.is_set(): 
                logger.critical("other thread(s) triggered. Cleaning up.")
                raise dms.observerTriggerException()

            logger.debug("trying to get frame from frameQueue...")
            frame = frameQueue.get()
            logger.debug("got frame from frameQueue.")
            try:
                logger.debug("trying to acquire detectorLock...")
                with detectorLock:
                    logger.debug("acquired detectorLock.")
                    face_locations = extract_faces(
                        img_path=frame,
                        detector_backend=args.detector_backend,
                        enforce_detection=False
                    )
                logger.debug("released detectorLock.")
                logger.info(f"Enumerated {len(face_locations)} face(s) in this frame.")

                if (len(face_locations) < args.min_faces) or ((args.max_faces != None) and (len(face_locations) > args.max_faces)):
                    logger.critical(f"TRIGGER: Number of faces outside of accepted bounds. Min: {args.min_faces} Max: {args.max_faces} Found: {len(face_locations)}")
                    raise dms.observerTriggerException()
                
            except ValueError:
                logger.warning("No faces detected in this frame.")
                if args.min_faces > 0:
                    logger.critical(f"TRIGGER: --min-faces ({args.min_faces}) mandates at least one face be present in every frame.")
                    raise dms.observerTriggerException()
            else:
                pass
            
            if face_locations is None:
                logger.warning("Skipping queue since no faces were detected.")
            elif (args.require_faces or args.reject_faces or args.reject_unknown or args.reject_emotions) and faceDetectionQueue:
                if args.noblock:
                    try:
                        logger.debug("trying to put frame, face_locations into faceDetectionQueue (--noblock)...")
                        faceDetectionQueue.put_nowait((frame,face_locations))
                        logger.debug("put frame, face_locations into faceDetectionQueue.")
                    except queue.Full:
                        logger.debug("faceDetectionQueue is full: --noblock flag set. Moving on.")
                else:
                    logger.debug("waiting to put frame, face_locations into faceDetectionQueue...")
                    faceDetectionQueue.put((frame,face_locations))
                    logger.debug("put frame, face_locations into faceDetectionQueue.")
            else:
                pass
            
    except BaseException as e:
        triggerEvent.set()
        logger.debug(traceback.print_exc())
        raise dms.observerTriggerException()
    
def extractFaceAndVerify(triggerEvent, detectorLock, identities, faceDetectionQueue, faceVerifResultsQueue=None):
    try:
        logger = logging.getLogger(__file__).getChild(__name__)

        logger.info("thread started.")
    
        logger.info("loading imports...")
        from deepface.DeepFace import verify, find
    
        if args.dump_frames:
            logger.warning("--dump-frames flag set. This will write past faces to disk, which may not be desired behaviour.")
            logger.info("--dump-frames: loading imports...")
            from cv2 import imwrite 
    
        logger.info("imports loaded.")

        while True:
            if triggerEvent.is_set(): 
                logger.critical("other thread(s) triggered. Cleaning up.")
                raise dms.observerTriggerException()

            logger.debug("trying to get frame, face_locations from faceDetectionQueue...")
            frame, face_locations = faceDetectionQueue.get()
            logger.debug("got frame, face_locations from faceDetectionQueue.")
        
            if args.require_faces: requiredFacesPresent = []
            for face in face_locations:
                logger.debug("awaiting detectorLock to crop frame to face...")
                with detectorLock:
                    logger.debug("detectorLock acquired.")
                    croppedFrame = frame[
                        face['facial_area']['y']:face['facial_area']['y']+face['facial_area']['h'],
                        face['facial_area']['x']:face['facial_area']['x']+face['facial_area']['w']
                    ]
                logger.debug("releasing detectorLock...")
                faceID = None
                faceVerified = False
                for identity in identities:
                    logger.debug(f"Checking to see if current frame matches with identity: {identity}")
                    if identity == None:
                        logger.debug("Current identity to check is None (unknown). This is impossible to verify; continuing to the next.")
                        pass
                    else:
                        logger.debug("awaiting detectorLock to verify cropped frame...")
                        with detectorLock:
                            logger.debug("detectorLock acquired.")

                            if os.path.isfile(identity):
                                faceVerified = verify(
                                    img1_path=croppedFrame,
                                    img2_path=identity,
                                    silent=True,
                                    enforce_detection=False,
                                    model_name=args.model_name
                                )['verified']

                            elif os.path.isdir(identity):
                                # Return true if any matches are made with the images in the folder, else false.
                                results = find(
                                    img_path=croppedFrame,
                                    db_path=identity,
                                    silent=True,
                                    enforce_detection=False,
                                    detector_backend=args.detector_backend,
                                    model_name=args.model_name
                                )
                                
                                if len(results) != 1:
                                    # Sometimes with detector backends, a face can be recognized but its boundaries poorly drawn,
                                    # such that the cropped frame has enough room to contain multiple candidates for recognition/detection
                                    # (false positive or no).
                                    # It'd be nice to have a clean solution here, and perhaps it doesn't happen on other detectors;
                                    # But opencv was used for dev and test due to performance limitations.
                                    # This might also cause unexpected results further down the line, so it's best to discard the frame entirely.
                                    # Unsure if the previous thread (enumFacesInFrame) solves this problem, but it is feasible that an 'overlap' could occur
                                    # (wherein a given face appears in both crops where it is 'identified' and crops where it is not, within the same frame).
                                    # This consideration is complex and does not currently appear to affect functionality of the FYP, so it will be discussed as future work.
                                    logger.critical("More than one face was detected in the cropped image. This is possible, but should never happen.")
                                    raise Exception

                                # In this instance, this would manifest as a list with several pandas dataframes, since they each represent one identity.
                                else:
                                    faceVerified = True if not results[0].empty else False 
                                    # returns pandas dataframe, so slightly different.
                                    # Some clarification; find() returns a list of pandas datasets,
                                    # corresponding to /each individual detected/ (regardless of recognizability).
                                    # This means that if a face remains unknown, it still holds an entry as an empty
                                    # pandas dataframe.
                        
                            else: raise Exception(f"{identity} appears to be neither a folder nor file. This should be caught during argument parsing.")

                        logger.debug("detectorLock released.")

                        if faceVerified:
                        
                            faceID = identity
                            logger.info(f"face verified as identity ({identity})")
                        
                            if args.dump_frames: # Prepare filename
                                # folders don't have file extensions so we kludge our way into having one.
                                if os.path.isdir(identity): filename = os.path.basename(identity.replace('\\','/').strip('/'))+'.jpg' 
                                elif os.path.isfile(identity): filename = os.path.basename(identity)

                            if args.known_faces:
                                if identity in args.known_faces:
                                    logger.info("--known-faces: face verified as identity, but it is neither required nor forbidden.")
                            
                                if args.dump_frames:
                                        logger.debug('--dump-frames: writing frame to ' + os.path.join(args.dump_frames,'extractFaceAndVerify','known',filename))
                                        if not os.path.exists(os.path.join(args.dump_frames,'extractFaceAndVerify','known')): 
                                            os.makedirs(os.path.join(args.dump_frames,'extractFaceAndVerify','known'))
                                        imwrite(os.path.join(args.dump_frames,'extractFaceAndVerify','known',filename), croppedFrame)
                            
                                if args.require_faces:
                                    if identity in args.required_faces and (identity not in requiredFacesPresent):
                                        requiredFacesPresent.append(identity)
                                        logger.info('--require-faces: Verified that required identity is present in this frame.')
                                    else:
                                        logger.warn('--require-faces: another face has already been verified as this identity in this frame...')
                                
                                    if args.dump_frames:
                                        logger.debug('--dump-frames: writing frame to ' + os.path.join(args.dump_frames,'extractFaceAndVerify','required',filename))
                                        if not os.path.exists(os.path.join(args.dump_frames,'extractFaceAndVerify','required')): 
                                            os.makedirs(os.path.join(args.dump_frames,'extractFaceAndVerify','required'))
                                        imwrite(os.path.join(args.dump_frames,'extractFaceAndVerify','required',filename), croppedFrame)
                            
                                if args.reject_faces:
                                    if identity in args.reject_faces:
                                    
                                        if args.dump_frames: 
                                            logger.debug('--dump-frames: writing frame to' + os.path.join(args.dump_frames,'extractFaceAndVerify','forbidden',filename))
                                            if not os.path.exists(os.path.join(args.dump_frames,'extractFaceAndVerify','forbidden')): 
                                                os.makedirs(os.path.join(args.dump_frames,'extractFaceAndVerify','forbidden'))
                                            imwrite(os.path.join(args.dump_frames,'extractFaceAndVerify','forbidden',filename), croppedFrame)
                                    
                                        logger.critical(f"TRIGGER: --reject-faces: face verified as forbidden identity ({identity}).")
                                        raise dms.observerTriggerException()
                        
                            else: logger.info(f"Face did not match with identity {identity}")

                            # Finally, we move onto the next face.
                            continue

                    if (faceID == None):     
                    
                        # Redundant?
                        # 'None' is placed in identities to allow for FER to be performed on unknown identities.
                        logger.warning("Identity unknown; face did not match with any that were provided.")

                        if args.dump_frames: 
                            logger.debug(f"--dump-frames: writing frame to {os.path.join(args.dump_frames,'extractFaceAndVerify','unknown.jpg')}")
                            if not os.path.exists(os.path.join(args.dump_frames,'extractFaceAndVerify')): os.makedirs(os.path.join(args.dump_frames,'extractFaceAndVerify'))
                            imwrite(os.path.join(args.dump_frames,'extractFaceAndVerify','unknown.jpg'), croppedFrame)

                        elif args.reject_unknown:
                            logger.critical("TRIGGER: --reject-unknown: face in frame was unrecognizable as a specific identity.")
                            raise dms.observerTriggerException()

                    if args.reject_emotions and faceVerifResultsQueue:
                        if args.noblock:
                        
                            try:
                                logger.debug("trying to put faceID, croppedFrame into faceVerifResultsQueue (--noblock)...")
                                faceDetectionQueue.put_nowait((frame,face_locations))
                                logger.debug("put faceID, croppedFrame into faceVerifResultsQueue.")
                            except queue.Full:
                                logger.debug("faceVerifResultsQueue is full: --noblock flag set. Moving on.")
                    
                        else:
                            logger.debug("waiting to put faceID, croppedFrame into faceVerifResultsQueue...")
                            faceVerifResultsQueue.put((faceID,croppedFrame))
                            logger.debug("put faceID, croppedFrame into faceVerifResultsQueue.")
                
                    else:
                        logger.debug("FER disabled. Skipping queue.")

                if args.require_faces and (set(args.require_faces) != requiredFacesPresent):
                    logger.critical("TRIGGER: --require-faces: Not all identities provided were present in this frame.")
                    raise dms.observerTriggerException()

                logger.info("Moving onto next face in queue.")

    except BaseException as e:
        triggerEvent.set()
        logger.debug(traceback.print_exc())
        raise dms.observerTriggerException()
                    
def determineFacialEmotion(triggerEvent, detectorLock, faceVerifResultsQueue, idEmotionPairDict):
    try:
        logger = logging.getLogger(__file__).getChild(__name__)

        logger.info("thread started.")
    
        logger.info("loading imports...")
        from deepface.DeepFace import analyze
        logger.info("imports loaded.")

        while True:
            if triggerEvent.is_set(): 
                logger.critical("other thread(s) triggered. Cleaning up.")
                raise dms.observerTriggerException()

            logger.debug("trying to get faceID, croppedFrame from faceVerifResultsQueue...")
            faceID, croppedFrame = faceVerifResultsQueue.get()
            logger.debug("got faceID, croppedFrame from faceVerifResultsQueue.")
    
            logger.debug("attempting to acquire detectorLock...")
            with detectorLock:
                logger.debug("acquired detectorLock.")
                analysis_results = analyze(
                    img_path=croppedFrame,
                    actions=["emotion"],
                    silent=True,
                    enforce_detection=False,
                    detector_backend=args.detector_backend
                )
            logger.debug("released detectorLock.")

            if len(analysis_results) != 1: 
                logger.critical("More than one face was detected in the cropped image. This should never happen.")
                # Actually, it's possible in some detector backends (openCV was used in dev) that a face can be /detected/,
                # but its 'coordinates' not accurately drawable. This has never happened in testing, but the possibility is there, so this is here to raise an exception.
                raise Exception
                        
            emotion_results = analysis_results[0] # We're cropping straight to the detected face, so we should only ever have one entry.

            emotionRatings = emotion_results['emotion']
            
            logger.debug("attempting to acquire FERLock...")
            with idEmotionPairDict[faceID]['lock']:
                logger.debug(f"acquired idEmotionLock for {faceID}.")
                # Because the calculateAverage threads drain facialEmotionQueue in batches, we do put_nowait() regardless of --noblock to avoid a deadlock.
                # We need FERLock because calculateAverage threads need to 'peek' the queue to see if a given item belongs to their identity without accidentally moving them out of order.
                try:
                    logger.debug("trying to put faceID, emotionRatings into facialEmotionQueue...")
                    idEmotionPairDict[faceID]['queue'].put_nowait(emotionRatings)
                    logger.debug("put faceID, emotionRatings in facialEmotionQueue.")
                except queue.Full:
                    logger.debug(f"Queue is full - releasing idEmotionLock for {faceID} to prevent deadlock.")
                    break
            logger.debug(f"released idEmotionLock for {faceID}.")

            logger.info(f"Finished FER for instance of {faceID}.")

    except BaseException as e:
        triggerEvent.set()
        logger.debug(traceback.print_exc())
        raise dms.observerTriggerException()

def calculateAverage(triggerEvent, idEmotionLock, identity, idEmotionBuffer, FERAvgQueue):
    try:
        logger = logging.getLogger(__file__).getChild(__name__)

        logger.info(f"thread started for {identity}.")
 
        while True:
            if triggerEvent.is_set(): 
                logger.critical("other thread(s) triggered. Cleaning up.")
                raise dms.observerTriggerException()

            idEmotionBufferList = []
            while len(idEmotionBufferList) < args.sliding_window_size:
                logger.debug(f"{identity} attempting to acquire idEmotionLock...")
                with idEmotionLock:
                    logger.debug(f"{identity} acquired idEmotionLock.")

                    if not idEmotionBuffer.full():
                        logger.debug(f"{identity} idEmotionBuffer state: {idEmotionBuffer.qsize()}/{args.sliding_window_size}")
                        pass
                    
                    elif idEmotionBuffer.full():
                        logger.info(f"{identity} idEmotionBuffer full")
                        while not idEmotionBuffer.empty():
                            idEmotionBufferList.append(idEmotionBuffer.get())
                        logger.info(f"{identity} idEmotionBuffer empty")

                logger.debug(f"{identity} released idEmotionLock.")

            # Now we calculate the averages and put them in the average queue.

            averageDict = {
                "identity": identity,
                "emotion": {
                    "anger": sum([x.get('angry') for x in idEmotionBufferList])/len(idEmotionBufferList),
                    "disgust": sum([x.get('disgust') for x in idEmotionBufferList])/len(idEmotionBufferList),
                    "fear": sum([x.get('fear') for x in idEmotionBufferList])/len(idEmotionBufferList),
                    "happy": sum([x.get('happy') for x in idEmotionBufferList])/len(idEmotionBufferList),
                    "sad": sum([x.get('sad') for x in idEmotionBufferList])/len(idEmotionBufferList),
                    "surprise": sum([x.get('surprise') for x in idEmotionBufferList])/len(idEmotionBufferList),
                    "neutral": sum([x.get('neutral') for x in idEmotionBufferList])/len(idEmotionBufferList)
                }
            }
            
            if args.noblock:
                try:    
                    logger.debug("trying to put averageDict into FERAvgQueue (--noblock)...")
                    FERAvgQueue.put_nowait(averageDict)
                    logger.debug("put averageDict into FERAvgQueue.")
                except queue.Full:
                    logger.debug("FERAvgQueue is full: --noblock flag set. Moving on.")
            else:
                logger.debug("waiting to put averageDict into FERAvgQueue...")
                FERAvgQueue.put(averageDict)
                logger.debug("put averageDict into FERAvgQueue.")

            logger.info(f"Average calculated for identity {identity} over this sliding window period ({args.sliding_window_size} occurences).")
    except BaseException as e:
        triggerEvent.set()
        logger.debug(traceback.print_exc())
        raise dms.observerTriggerException()

def calcForbidden(triggerEvent, FERAvgQueue, forbidden_emotions):
    try:
    
        logger = logging.getLogger(__file__).getChild(__name__)
    
        logger.info("Thread started.")

        if triggerEvent.is_set(): 
            logger.critical("other thread(s) triggered. Cleaning up.")
            raise dms.observerTriggerException()

        # We don't need an operations pool here because it's the end of the line.

        while True:
            logger.debug('getting new emotional average from avgEmotionDict...')
            avgEmotionDict = FERAvgQueue.get()
            
            logger.info(f"checking average emotional state of {avgEmotionDict.get('identity')}...")

            for emotion,value in avgEmotionDict.get('emotion').items():
                if (emotion in forbidden_emotions) and (value >= forbidden_emotions.get(emotion)):
                    logger.critical(f"TRIGGER: Identity {avgEmotionDict.get('identity')} held forbidden emotion ({emotion}) over the last {args.sliding_window_size} frame(s) in which they were identified with average strength of {value}, bypassing the limit ({forbidden_emotions.get(emotion)}) by {value-forbidden_emotions.get(emotion)}.")
                    raise dms.observerTriggerException()
                elif (emotion in forbidden_emotions):
                    logger.info(f"{avgEmotionDict.get('identity')}: monitored emotion ({emotion}) within acceptable bounds (avg. {value}, limit: {forbidden_emotions.get(emotion)})")
                else:
                    logger.debug(f"{avgEmotionDict.get('identity')}: {emotion} not forbidden. Skipping.")
            
            logger.info(f"no forbidden emotions crossed threshold for {avgEmotionDict.get('identity')} over the last {args.sliding_window_size} frames in which they were identified.")

    except BaseException as e:
        triggerEvent.set()
        logger.debug(traceback.print_exc())
        raise dms.observerTriggerException()

def observerFunction():
    try:
        logger = logging.getLogger(__file__).getChild(__name__)
        
        logger.info("Started function.")
    
        asyncList = []

        detectorLock = multiprocessing.Manager().Lock()

        triggerEvent = multiprocessing.Manager().Event()

        identities = [None]

        if args.known_faces:
            for knownFace in args.known_faces:
                if os.path.exists(knownFace) and (knownFace not in IGNORE_FILES):
                    identities.append(knownFace)

        if args.require_faces:
            for requiredFace in args.require_faces:
                if os.path.exists(requiredFace) and (requiredFace not in IGNORE_FILES):
                    identities.append(requiredFace)

            if args.reject_faces:
                for rejectFace in args.reject_faces:
                    if os.path.exists(rejectFace) and (rejectFace not in IGNORE_FILES):
                        identities.append(rejectFace)

        # Why are we removing duplicates and casting to tuple instead of just using a set?
        # Sets are unordered, and in practice this meant that identities shuffled about a bit from
        # observerFunction() to extractFaceAndVerify().
        # Since we iterate over this container in that function, it seemed a good idea to remove this
        # non-determinable area of ambiguity, even though it wasn't causing any critical bugs.
                        
        identities = tuple(dict.fromkeys(identities))
        
        if (args.reject_noframe or args.max_faces or args.require_faces or args.reject_faces or args.reject_unknown or args.reject_emotions):
            if (args.max_faces or args.require_faces or args.reject_faces or args.reject_unknown or args.reject_emotions):
                frameQueue = multiprocessing.Manager().Queue(maxsize=args.max_buffer_size)
            else: frameQueue = None
            asyncList.append(multiprocessing.Process(target=getFrameFromWebcam, args=(triggerEvent, frameQueue)))
            logger.debug("added getFrameFromWebcam(triggerEvent, frameQueue) to asyncList.")


        if (args.max_faces or args.require_faces or args.reject_faces or args.reject_unknown or args.reject_emotions):
            if (args.require_faces or args.reject_faces or args.reject_unknown or args.reject_emotions):
                faceDetectionQueue = multiprocessing.Manager().Queue(maxsize=args.max_buffer_size)
            else: faceDetectionQueue = None
            asyncList.append(threading.Thread(target=enumFacesInFrame, args=(triggerEvent, detectorLock, frameQueue, faceDetectionQueue)))
            logger.debug("added enumFacesInFrame(triggerEvent, detectorLock, frameQueue, faceDetectionQueue) to asyncList.")
        
        if (args.require_faces or args.reject_faces or args.reject_unknown or args.reject_emotions):
            if args.reject_emotions: 
                faceVerifResultsQueue = multiprocessing.Manager().Queue(maxsize=args.max_buffer_size)
            else: 
                faceVerifResultsQueue = None
            asyncList.append(threading.Thread(target=extractFaceAndVerify, args=(triggerEvent, detectorLock, identities, faceDetectionQueue, faceVerifResultsQueue)))
            logger.debug("added extractFaceAndVerify(triggerEvent, detectorLock, identities, faceDetectionQueue, faceVerifResultsQueue) to asyncList.")
        
        if args.reject_emotions:
            FERAvgQueue = multiprocessing.Manager().Queue(maxsize=args.max_buffer_size)
            logger.debug("created FERAvgQueue.")

            idEmotionPairDict = dict()
            for identity in identities:
                idEmotionPairDict.update({
                    identity: {
                        "lock": multiprocessing.Manager().Lock(),
                        "queue": multiprocessing.Manager().Queue(maxsize=args.sliding_window_size)
                    }
                })
                asyncList.append(multiprocessing.Process(target=calculateAverage, args=(triggerEvent, idEmotionPairDict[identity]['lock'], identity, idEmotionPairDict[identity]['queue'], FERAvgQueue)))
                logger.debug(f"added calculateAverage(triggerEvent, idEmotionLock, '{identity}', idEmotionBuffer, FERAvgQueue) to asyncList.")
            
            asyncList.append(threading.Thread(target=determineFacialEmotion, args=(triggerEvent, detectorLock, faceVerifResultsQueue, idEmotionPairDict)))
            logger.debug("added determineFacialEmotion(triggerEvent, detectorLock, faceVerifResultsQueue, idEmotionPairDict) to asyncList.")
        
            asyncList.append(multiprocessing.Process(target=calcForbidden, args=(triggerEvent, FERAvgQueue, forbidden_emotions)))
            logger.debug("added calcForbidden(triggerEvent, FERAvgQueue, forbidden_emotions) to asyncList.")

        # We still have to run this when not verifying faces to extract the faces for FER.
        if (args.require_faces or args.reject_faces or args.reject_unknown or args.reject_emotions):
            logger.debug("added extractFaceAndVerify(faceDetectionQueue,faceVerifResultsQueue) to asyncList.")


        
        logger.debug(f"starting all processes in asyncList ({len(asyncList)}).")
        for process in asyncList: process.start()
        logger.debug(f"successfully started all processes.")

        while True:
            for process in asyncList:
                if not process.is_alive(): # Process has terminated, we should trigger!
                    logger.critical("thread exited - terminating pool workers!")
                    raise dms.observerTriggerException()
                else:
                    logger.debug("Process appears to be still alive...")
            logger.debug("all processes appear to still be running...")
            
    except BaseException as e:
        logger.debug("exception received!")
        logger.debug(traceback.print_exc())        
        logger.debug("closing all processes.")
        triggerEvent.set()
        for process in asyncList:
            logger.debug("waiting for process to exit cleanly...")
            waitOutcome = process.join(timeout=1) # Arbitrary timeout
            if waitOutcome is not None:
                logger.warning("process did not exit cleanly within timeout period.")
                if type(process) == threading.Thread:
                    logger.debug("Thread process, so cannot terminate manually. Daemonizing as alternative.")
                    process.daemon = True
                else:
                    try:
                        process.terminate() # Close the process.
                    except ValueError: logger.info('Process already closed...?')
                    logger.info("Joining terminated process to confirm closure.")
                    process.join() # Ensure it has actually closed to prevent zombie processes.
                    logger.info("Process appears to have exited with cleanup, but this is not guaranteed.")
        raise dms.observerTriggerException()

if __name__ == "__main__":
    try:
        logger = logging.getLogger(__file__).getChild(__name__)

        logger.debug('initialising obsProcess...')
        dms.obsProcess(
            args.host,
            args.port,
            func=observerFunction,
        )
        logger.debug('obsProcess initialised.')

    except BaseException as e:
        logger.debug(traceback.print_exc())
        raise dms.observerTriggerException()