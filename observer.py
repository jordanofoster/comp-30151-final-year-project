import dms
import signal, traceback, time, argparse, sys, os, multiprocessing, glob, logging, datetime, queue

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
        raise argparse.ArgumentTypeError(f"[{os.path.os.path.basename(__file__)}][{__name__}][{datetime.datetime.now()}] Forbidden emotions must be in format emotion:min_score.\nmin_score must be a valid float between 0-100.\nemotion must be a valid emotion out of the following: happy, neutral, surprise, sad, angry, fear, disgust.")

def _lim_faces(f):
    if int(f) < 0:
        raise argparse.ArgumentTypeError("Face bounds (--min-faces and --max-faces) cannot be less than 0.")
    else: return int(f)

def _face_path(p):
    if os.path.isfile(p) or os.path.isdir(p): return [p]
    else: raise argparse.ArgumentTypeError(f"Identity flags (--known-faces, --require-faces and --reject-faces) must be valid filenames or paths. {p} is an invalid path.")

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

parser.add_argument('--sliding-window-size', action="store", default=10, type=int, required=True)
parser.add_argument('--max-buffer-size', action="store", default=0, type=int)

parser.add_argument('--dump-frames', action="store_true")
parser.add_argument('--log-level', choices=[logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL], default="INFO", type=_log_level)
parser.add_argument('--log-file', action="store", default=None)

args = parser.parse_args()

if args.dump_frames:
    frameDumpDir = './debug'

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

identitySet = {None}

if args.known_faces:
    for knownFace in args.known_faces:
        if os.path.exists(knownFace) and (knownFace not in IGNORE_FILES):
            identitySet.add(knownFace)

if args.require_faces:
    for requiredFace in args.require_faces:
        if os.path.exists(requiredFace) and (requiredFace not in IGNORE_FILES):
            identitySet.add(requiredFace)

if args.reject_faces:
    for rejectFace in args.reject_faces:
        if os.path.exists(rejectFace) and (rejectFace not in IGNORE_FILES):
            identitySet.add(rejectFace)

if args.log_file: logging.basicConfig(filename=args.log_file, level=args.log_level)
elif args.log_level < logging.WARNING: logging.basicConfig(stream=sys.stdout, level=args.log_level, format=dms.logfmt, datefmt=dms.datefmt)
else: logging.basicConfig(stream=sys.stderr, level=args.log_level, format=dms.logfmt, datefmt=dms.datefmt)

def getFrameFromWebcam(obsTriggered, frameQueue):
    logger = logging.getLogger(__file__).getChild(__name__)
    
    logger.info("thread started.")

    logger.info("loading imports...")
    from cv2 import VideoCapture
    logger.info("imports loaded.")
    
    try:
        video_capture = VideoCapture(0)

        while True:
            if obsTriggered.is_set():
                logger.critical("other thread(s) triggered. Cleaning up.")
                raise dms.observerTriggerException

            logger.debug("trying to get frame from webcam...")
            ret, frame = video_capture.read()
            logger.debug("got frame from webcam.")
            if not ret:
                logger.warn("Could not get frame from webcam.")
                if args.reject_noframe:
                    logger.critical("TRIGGER: --reject-noframe flag set.")
                    raise dms.observerTriggerException
                else:
                    pass
            else:
                if args.noblock:
                    try:
                        logger.debug("trying to put frame into frameQueue (--noblock)...")
                        frameQueue.put_nowait(frame)
                        logger.debug("put frame into frameQueue.")
                    except queue.Full:
                        logger.warn("frameQueue is full: --noblock flag set. Moving on.")
                else:
                    logger.debug("waiting to put frame into frameQueue...")
                    frameQueue.put(frame)
                    logger.debug("put frame into framequeue.")
                
    except Exception as e:
        logger.debug(traceback.print_exc())
        return
    
def enumFacesInFrame(obsTriggered, detectorLock, frameQueue, faceDetectionQueue=None):
    logger = logging.getLogger(__file__).getChild(__name__)

    logger.info("thread started.")
    
    logger.info("loading imports...")
    from deepface.DeepFace import extract_faces
    logger.info("imports loaded.")
    
    try:
        while True:
            if obsTriggered.is_set(): 
                logger.critical("other thread(s) triggered. Cleaning up.")
                raise dms.observerTriggerException

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
                logger.debug(f"Enumerated {len(face_locations)} face(s) in this frame.")

                if (len(face_locations) < args.min_faces) or ((args.max_faces != None) and (len(face_locations) > args.max_faces)):
                    logger.critical(f"TRIGGER: Number of faces outside of accepted bounds. Min: {args.min_faces} Max: {args.max_faces} Found: {len(face_locations)}")
                    raise dms.observerTriggerException
                
            except ValueError:
                logger.warn("No faces detected in this frame.")
                if args.min_faces > 0:
                    logger.critical(f"TRIGGER: --min-faces ({args.min_faces}) mandates at least one face be present in every frame.")
                    raise dms.observerTriggerException
            else:
                pass
            
            if face_locations is None:
                logger.warn("Skipping queue since no faces were detected.")
            elif (args.require_faces or args.reject_faces or args.reject_unknown or args.reject_emotions) and faceDetectionQueue:
                if args.noblock:
                    try:
                        logger.debug("trying to put frame, face_locations into faceDetectionQueue (--noblock)...")
                        faceDetectionQueue.put_nowait((frame,face_locations))
                        logger.debug("put frame, face_locations into faceDetectionQueue.")
                    except queue.Full:
                        logger.warn("faceDetectionQueue is full: --noblock flag set. Moving on.")
                else:
                    logger.debug("waiting to put frame, face_locations into faceDetectionQueue...")
                    faceDetectionQueue.put((frame,face_locations))
                    logger.debug("put frame, face_locations into faceDetectionQueue.")
            else:
                pass

    except Exception as e:
        logger.debug(traceback.print_exc())
        return
    
def extractFaceAndVerify(obsTriggered, detectorLock, faceDetectionQueue, faceVerifResultsQueue=None):
    logger = logging.getLogger(__file__).getChild(__name__)

    logger.info("thread started.")
    
    logger.info("loading imports...")
    from deepface.DeepFace import verify, find
    if args.dump_frames:
        logger.warn("--dump-frames flag set. This will write past faces to disk, which may not be desired behaviour.")
        logger.info("Loading --dump-frames imports...")
        from cv2 import imwrite 
    logger.info("[extractFacesAndVerify] imports loaded.")
    
    try:
        while True:
            if obsTriggered.is_set(): 
                logger.critical("other thread(s) triggered. Cleaning up.")
                raise dms.observerTriggerException

            logger.debug("trying to get frame, face_locations from faceDetectionQueue...")
            frame, face_locations = faceDetectionQueue.get()
            logger.debug("got frame, face_locations from faceDetectionQueue.")
        
            if args.require_faces: requiredFacePresent = False
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
                for identity in identitySet:
                    logger.debug(f"Checking to see if current frame matches with identity: {identity}")
                    if identity == None:
                        logger.debug("Current identity to check is None (unknown). This is impossible to verify; pass.")
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
                                faceVerified = True if (
                                    len(find(
                                        img_path=croppedFrame,
                                            db_path=identity,
                                            silent=True,
                                            enforce_detection=False,
                                            detector_backend=args.detector_backend,
                                            model_name=args.model_name
                                        )) > 0
                                ) else False
                                
                            else: raise Exception(f"{identity} appears to be neither a folder nor file. This should be caught during argument parsing.")

                        logger.debug("detectorLock released.")

                        if faceVerified:
                            faceID = identity
                            logger.debug(f"face matched with identity ({os.path.basename(identity)})")
                            if args.known_faces:
                                if identity in args.known_faces:
                                    logger.info("identity recognized, but not required or forbidden.")
                            if args.require_faces:
                                if identity in args.required_faces and (requiredFacePresent == False):
                                    requiredFacePresent = True
                                    logger.info(f"required identity recognized.")
                                if args.dump_frames:
                                    logger.debug(f"--dump-frames set: writing frame to {frameDumpDir}/{__name__}/required/{os.path.basename(identity)}")
                                    if not os.path.exists(f"{frameDumpDir}/{__name__}/required"): os.makedirs(f"{frameDumpDir}/{__name__}/required")
                                    imwrite(f"{frameDumpDir}/{__name__}/required/{os.path.basename(identity)}.jpg", croppedFrame)
                            if args.reject_faces:
                                if identity in args.reject_faces:
                                    if args.dump_frames: 
                                        logging.debug(f"--dump-frames set: writing frame to {frameDumpDir}/{__name__}/forbidden/{os.path.basename(identity)}")
                                        if not os.path.exists(f"{frameDumpDir}/{__name__}/forbidden"): os.makedirs(f"{frameDumpDir}/{__name__}/forbidden")
                                        imwrite(f"{frameDumpDir}/{__name__}/forbidden/{os.path.basename(identity)}", croppedFrame)
                                    logger.critical("TRIGGER: forbidden identity ({os.path.basename(identity)}) detected in frame.")
                                    raise dms.observerTriggerException
                        else: logger.debug(f"Face did not match with identity {identity}")

                if (faceID == None):     
                    
                    logger.warn("face did not match with any identities provided.")

                    if args.require_faces and not requiredFacePresent:
                        logger.critical("TRIGGER: --require-faces argument provided, but no required faces in frame.")
                        raise dms.observerTriggerException
                    elif args.reject_unknown:
                        logger.critical("TRIGGER: --reject-unknown flag set.")
                        raise dms.observerTriggerException
                    
                    if args.dump_frames: 
                        logger.debug(f"--dump-frames set: writing frame to {frameDumpDir}/{__name__}/Unknown.jpg")
                        if not os.path.exists(f"{frameDumpDir}/{__name__}"): os.makedirs(f"{frameDumpDir}/{__name__}")
                        imwrite(f"{frameDumpDir}/{__name__}/Unknown.jpg", croppedFrame)

        
                if args.reject_emotions and faceVerifResultsQueue:
                    if args.noblock:
                        try:
                            logger.debug("trying to put faceID, croppedFrame into faceVerifResultsQueue (--noblock)...")
                            faceDetectionQueue.put_nowait((frame,face_locations))
                            logger.debug("put faceID, croppedFrame into faceVerifResultsQueue.")
                        except queue.Full:
                            logger.warn("faceVerifResultsQueue is full: --noblock flag set. Moving on.")
                    else:
                        logger.debug("waiting to put faceID, croppedFrame into faceVerifResultsQueue...")
                        faceVerifResultsQueue.put((faceID,croppedFrame))
                        logger.debug("put faceID, croppedFrame into faceVerifResultsQueue.")
                else:
                    logger.debug("FER disabled. Skipping queue.")

    except Exception as e:
        logger.debug(traceback.print_exc())
        return
                    
def determineFacialEmotion(obsTriggered, detectorLock, faceVerifResultsQueue, idEmotionPairDict):
    logger = logging.getLogger(__file__).getChild(__name__)

    logger.info("thread started.")
    
    logger.info("loading imports...")
    from deepface.DeepFace import analyze
    logger.info("imports loaded.")

    try:
        while True:
            if obsTriggered.is_set(): 
                logger.critical("other thread(s) triggered. Cleaning up.")
                raise dms.observerTriggerException

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
                    logger.warn(f"Queue is full - releasing idEmotionLock for {faceID} to prevent deadlock.")
                    break
            logger.debug(f"released idEmotionLock for {faceID}.")

    except Exception as e:
        logger.debug(traceback.print_exc())
        return

def calculateAverage(obsTriggered, idEmotionLock, identity, idEmotionBuffer, FERAvgQueue):
    logger = logging.getLogger(__file__).getChild(__name__)

    logger.info(f"thread started for {identity}.")

    try:
        while True:
            if obsTriggered.is_set(): 
                logger.critical("other thread(s) triggered. Cleaning up.")
                raise dms.observerTriggerException

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
                    logger.warn("FERAvgQueue is full: --noblock flag set. Moving on.")
            else:
                logger.debug("waiting to put averageDict into FERAvgQueue...")
                FERAvgQueue.put(averageDict)
                logger.debug("put averageDict into FERAvgQueue.")

    except Exception as e:
        logger.debug(traceback.print_exc())
        return

def calcForbidden(obsTriggered, FERAvgQueue, forbidden_emotions):
    logger = logging.getLogger(__file__).getChild(__name__)
    
    logger.info("Thread started.")

    try:
        if obsTriggered.is_set(): 
            logger.critical("other thread(s) triggered. Cleaning up.")
            raise dms.observerTriggerException

        # We don't need an operations pool here because it's the end of the line.

        while True:
            avgEmotionDict = FERAvgQueue.get()
            
            logger.info(f"checking average emotional state of {avgEmotionDict.get('identity')}...")

            for emotion,value in avgEmotionDict.get('emotion').items():
                if (emotion in forbidden_emotions) and (value >= forbidden_emotions.get(emotion)):
                    logger.critical(f"TRIGGER: Identity {avgEmotionDict.get('identity')} held forbidden emotion ({emotion}) over the last {args.sliding_window_size} frame(s) in which they were identified with average strength of {value}, bypassing the limit ({forbidden_emotions.get(emotion)}) by {value-forbidden_emotions.get(emotion)}.")
                    raise dms.obsTriggerException
                elif (emotion in forbidden_emotions):
                    logger.info(f"{avgEmotionDict.get('identity')}: monitored emotion ({emotion}) within acceptable bounds (avg. {value}, limit: {forbidden_emotions.get(emotion)})")
                else:
                    logger.debug(f"{avgEmotionDict.get('identity')}: {emotion} not forbidden. Skipping.")
            
            logger.info(f"no forbidden emotions crossed threshold for {avgEmotionDict.get('identity')} over the last {args.sliding_window_size} frames in which they were identified.")

    except Exception as e:
        logger.debug(traceback.print_exc())
        return

def observerFunction():
    logger = logging.getLogger(__file__).getChild(__name__)

    logger.info("Started function.")
    
    try:
        asyncList = []

        detectorLock = multiprocessing.Manager().Lock()
        FERLock = multiprocessing.Manager().Lock()

        obsTriggered = multiprocessing.Manager().Event()

        if (args.reject_noframe or args.max_faces or args.require_faces or args.reject_faces or args.reject_unknown or args.reject_emotions):
            if (args.max_faces or args.require_faces or args.reject_faces or args.reject_unknown or args.reject_emotions):
                frameQueue = multiprocessing.Manager().Queue(maxsize=args.max_buffer_size)
            else: frameQueue = None
            asyncList.append(multiprocessing.Process(target=getFrameFromWebcam, args=(obsTriggered, frameQueue)))
            logger.debug("added getFrameFromWebcam(obsTriggered, frameQueue) to asyncList.")


        if (args.max_faces or args.require_faces or args.reject_faces or args.reject_unknown or args.reject_emotions):
            if (args.require_faces or args.reject_faces or args.reject_unknown or args.reject_emotions):
                faceDetectionQueue = multiprocessing.Manager().Queue(maxsize=args.max_buffer_size)
            else: faceDetectionQueue = None
            asyncList.append(multiprocessing.Process(target=enumFacesInFrame, args=(obsTriggered, detectorLock, frameQueue, faceDetectionQueue)))
            logger.debug("added enumFacesInFrame(obsTriggered, detectorLock, frameQueue, faceDetectionQueue) to asyncList.")
        
        if (args.require_faces or args.reject_faces or args.reject_unknown or args.reject_emotions):
            if args.reject_emotions: 
                faceVerifResultsQueue = multiprocessing.Manager().Queue(maxsize=args.max_buffer_size)
            else: 
                faceVerifResultsQueue = None
            asyncList.append(multiprocessing.Process(target=extractFaceAndVerify, args=(obsTriggered, detectorLock, faceDetectionQueue, faceVerifResultsQueue)))
            logger.debug("added extractFaceAndVerify(obsTriggered, detectorLock, faceDetectionQueue, faceVerifResultsQueue) to asyncList.")
        
        if args.reject_emotions:
            FERAvgQueue = multiprocessing.Manager().Queue(maxsize=args.max_buffer_size)
            logger.debug("created FERAvgQueue.")

            idEmotionPairDict = dict()
            for identity in identitySet:
                idEmotionPairDict.update({
                    identity: {
                        "lock": multiprocessing.Manager().Lock(),
                        "queue": multiprocessing.Manager().Queue(maxsize=args.sliding_window_size)
                    }
                })
                asyncList.append(multiprocessing.Process(target=calculateAverage, args=(obsTriggered, idEmotionPairDict[identity]['lock'], identity, idEmotionPairDict[identity]['queue'], FERAvgQueue)))
                logger.debug(f"added calculateAverage(obsTriggered, idEmotionLock, '{identity}', idEmotionBuffer, FERAvgQueue) to asyncList.")
            
            asyncList.append(multiprocessing.Process(target=determineFacialEmotion, args=(obsTriggered, detectorLock, faceVerifResultsQueue, idEmotionPairDict)))
            logger.debug("added determineFacialEmotion(obsTriggered, detectorLock, faceVerifResultsQueue, idEmotionPairDict) to asyncList.")
        
            asyncList.append(multiprocessing.Process(target=calcForbidden, args=(obsTriggered, FERAvgQueue, forbidden_emotions)))
            logger.debug("added calcForbidden(obsTriggered, FERAvgQueue, forbidden_emotions) to asyncList.")

        # We still have to run this when not verifying faces to extract the faces for FER.
        if (args.require_faces or args.reject_faces or args.reject_unknown or args.reject_emotions):
            logger.debug("added extractFaceAndVerify(faceDetectionQueue,faceVerifResultsQueue) to asyncList.")


        
        logger.debug(f"starting all processes in asyncList ({len(asyncList)}).")
        for process in asyncList: process.start()
        logger.debug(f"successfully started all processes.")

        while True:
            for process in asyncList:
                if process.exitcode != None: # Process has terminated, we should trigger!
                    logger.critical("thread exited - terminating pool workers!")
                    raise dms.observerTriggerException
                else:
                    logger.debug("Process appears to be still alive...")
            logger.debug("all processes appear to still be running...")
            
    except Exception as e:
        logger.debug("exception received!")
        logger.debug(traceback.print_exc())        
        logger.debug("closing all processes.")
        obsTriggered.set()
        for process in asyncList:
            logger.debug("waiting for process to exit cleanly...")
            waitOutcome = process.join(timeout=5) # Arbitrary timeout
            if waitOutcome is not None:
                logger.warn("process did not exit cleanly within timeout period. Terminating; some resources may not be freed.")
                process.close() # Close the process.
                logger.info("Joining terminated process to confirm closure.")
                process.join() # Ensure it has actually closed to prevent zombie processes.
                logger.info("Process appears to have exited with cleanup, but this is not guaranteed.")
        raise dms.observerTriggerException

if __name__ == "__main__":
    dms.obsProcess(
        args.host,
        args.port,
        func=observerFunction,
    )