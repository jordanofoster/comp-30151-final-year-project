from dms import obsProcess
import signal, traceback,time, argparse, datetime, pathlib, sys, os, multiprocessing, threading
from subprocess import DEVNULL
import queue

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
parser.add_argument('--known-faces', nargs="+", type=str)
#parser.add_argument('--trigger-events', choices=["CAMERA_BLOCK", "UNKNOWN_FACE", "VERIFICATION_FAILED", "EMOTION_DETECTION"])
#parser.add_argumnt('--detector-backend', default='opencv')

args = parser.parse_args()

MODEL_NAME='GhostFaceNet'
DETECTOR_BACKEND='opencv'

ignoreList = (
    "ds_vggface_opencv_v2.pkl"
    "ds_vggface_ssd_v2.pkl"
    "ds_ghostfacenet_opencv_v2.pkl"
)

openCVLock = threading.Lock()

SLIDING_WINDOW_SIZE=10
MAX_BUFFER_SIZE=100

assert (SLIDING_WINDOW_SIZE <= MAX_BUFFER_SIZE), "Sliding window size larger than queue buffer!" 

def getFrameFromWebcam(frameQueue):
    print("[getFrameFromWebcam] loading imports...")
    from cv2 import VideoCapture
    print("[getFrameFromWebcam] imports loaded.")

    video_capture = VideoCapture(0)

    while True:
        ret, frame = video_capture.read()
        #print("[getFrameFromWebcam] got frame from webcam.")
        if not ret:
            print("[getFrameFromWebcam] FATAL: lost access to webcam!")
            signal.raise_signal(signal.SIGTERM)
            sys.exit(signal.SIGTERM)
        else: 
            #imwrite("debug/frame.jpg",frame)
            frameQueue.put(frame)
            #print("[getFrameFromWebcam] put webcam frame into frameQueue.")

def enumFacesInFrame(frameQueue, faceDetectionQueue):
    print("[enumFacesInFrame] loading imports...")
    from deepface.DeepFace import extract_faces
    print("[enumFacesInFrame] imports loaded.")

    while True:
        frame = frameQueue.get()
        #print("[enumFacesInFrame] got frame from frameQueue.")
        try:
            with openCVLock:
                face_locations = extract_faces(
                    img_path=frame,
                    detector_backend=DETECTOR_BACKEND
                )
            print(f"[enumFacesInFrame] Enumerated {len(face_locations)} face(s) in this frame.")
        except ValueError:
            print("[enumFacesInFrame] No faces detected in this frame.")
            face_locations = None
        finally:
            faceDetectionQueue.put((frame, face_locations))
            #print("[enumFacesInFrame] put tuple into faceDetectionQueue.")

def extractFaceAndVerify(faceDetectionQueue, identityMap, faceVerifResultsQueue):
    print("[extractFaceAndVerify] loading imports...")
    from deepface.DeepFace import verify
    print("[extractFacesAndVerify] imports loaded.")

    while True:
        facesIdentifiedTuple = faceDetectionQueue.get()
        #print("[extractFaceAndVerify] got tuple from faceDetectionQueue.")
        if facesIdentifiedTuple[1] is None: # No faces detected
            pass
        else:
            for face in facesIdentifiedTuple[1]:
                print("[extractFaceAndVerify] Trying to acquire lock to crop frame to face with OpenCV...")
                #with openCVLock:
                #    print("[extractFaceAndVerify] Acquired cv2 Crop Lock.")
                croppedFrame = facesIdentifiedTuple[0][
                    face['facial_area']['y']:face['facial_area']['y']+face['facial_area']['h'],
                    face['facial_area']['x']:face['facial_area']['x']+face['facial_area']['w']
                ]
                #print("[extractFaceAndVerify] released cv2 Crop Lock.")
                faceID = None
                for identity in identityMap.keys():
                    if identity == None:
                        pass
                    else:
                        faceVerified = verify(
                            img1_path=croppedFrame,
                            img2_path=f"known_faces/{identity}",
                            silent=True,
                            enforce_detection=False,
                            model_name=MODEL_NAME
                        )['verified']

                        if faceVerified:
                            print(f"[extractFaceAndVerify] Face match: {identity}")
                            faceID = identity
                            break
                        else:
                            print("[extractFaceAndVerify] Face did not match with any identities.")
                            pass

                faceVerifResultsQueue.put((croppedFrame,face,faceVerified,faceID))
                #print("[extractFaceAndVerify] put tuple into faceVerifResultsQueue.")
                
def determineFacialEmotion(faceVerifResultsQueue, facialEmotionQueue):
    try:
        print("[determineFacialEmotions] loading imports...")
        from deepface.DeepFace import analyze
        print("[determineFacialEmotion] imports loaded.")

        while True:
            faceVerifTuple = faceVerifResultsQueue.get()
            #print("[determineFacialEmotion] got tuple from faceVerifResultsQueue.")
            if (faceVerifTuple[2] == False) and (faceVerifTuple[3] == None):
                continue
    
            faceID = faceVerifTuple[3]
            with openCVLock:
                analysis_results = analyze(
                    img_path=faceVerifTuple[0],
                    actions=["emotion"],
                    silent=True,
                    enforce_detection=False,
                    detector_backend=DETECTOR_BACKEND
                )

            if len(analysis_results) != 1: raise Exception("[determineFacialEmotion] FATAL: More than one face was detected in the cropped image. This should not happen.")
                        
            emotion_results = analysis_results[0] # We're cropping straight to the detected face, so we should only ever have one entry.

            emotionRatings = emotion_results['emotion']
            
            facialEmotionQueue.put((faceID,emotionRatings))
            #print("[determineFacialEmotion] put tuple in facialEmotionQueue.")
    except Exception as e:
        traceback.print_exc()
        print(e)
        signal.raise_signal(signal.SIGTERM)
        sys.exit(signal.SIGTERM)

def putEmotionIntoIdentityMap(facialEmotionQueue, identityMap):
    try:
        while True:
            #print("[putEmotionIntoIdentityMap] trying to get emotionTuple from facialEmotionQueue...")
            emotionTuple = facialEmotionQueue.get()
            #print("[putEmotionIntoIdentityMap] got emotionTuple from facialEmotionQueue.")
            identity = emotionTuple[0]
            ratings = emotionTuple[1]

            print("[putEmotionIntoIdentityMap] Waiting to acquire identityMap lock...")
            with identityMap[identity]['lock']:
                print("[putEmotionIntoIdentityMap] Acquired identityMap lock.")
                for emotion in ratings.keys():
                    identityMap[identity]['values'][emotion].put(ratings[emotion])
                print("[putEmotionIntoIdentityMap] Released identityMap lock.")

    except Exception as e:
        traceback.print_exc()
        print(e)
        signal.raise_signal(signal.SIGTERM)
        sys.exit(signal.SIGTERM)

def calculateAverage(identityMap, identity, FERAvgQueue):
    try:
        while True:
            identityAvg = {
                "happy": float(),
                "neutral": float(),
                "surprise": float(),
                "sad": float(),
                "angry": float(),
                "fear": float(),
                "disgust": float()
            }

            buffersReady = False

            while not buffersReady:
                buffersReady = True
                for emotion in identityMap[identity]['values'].keys():
                    if not identityMap[identity]['values'][emotion].full():
                        #print(f"[calculateAverager] {emotion}: {identityMap[identity]['values'][emotion].qsize()}/{identityMap[identity]['values'][emotion].maxsize}")
                        #print(f"[calculateAverage] emotion buffers not full for identity {identity}. Looping.")
                        time.sleep(1)
                        buffersReady = False
                        break

                if buffersReady:
                    print("[calculateAverage] Waiting to acquire identityMap lock...")
                    with identityMap[identity]['lock']:
                        print("[calculateAverage] Acquired identityMap lock.")
                        for emotion in identityMap[identity]['values'].keys():
                            slidingWindow = []
                            while not identityMap[identity]['values'][emotion].empty():
                                slidingWindow.append(identityMap[identity]['values'][emotion].get())
                            identityAvg[emotion] = sum(slidingWindow)/len(slidingWindow)
                    print("[calculateAverage] Released identityMap lock.")
                    
                    FERAvgQueue.put((identity,identityAvg))
                    #print("[calculateAverage] put tuple into FERAvgQueue.")
    except Exception as e:
        traceback.print_exc()
        print(e)
        signal.raise_signal(signal.SIGTERM)
        sys.exit(signal.SIGTERM)

def calcForbidden(FERAvgQueue, forbidden_emotions):
    try:
        while True:
            FERAvgQueueTuple = FERAvgQueue.get()
            #print("[calcForbidden] got tuple from FERAvgQueue.")
            identity = FERAvgQueueTuple[0]
            FERAvg = FERAvgQueueTuple[1]

            for emotion in FERAvg.keys():
                if (emotion in forbidden_emotions.keys()) and (FERAvg[emotion] >= forbidden_emotions[emotion]):
                    print(f"Forbidden emotion ({emotion}) exceeded minimum confidence ({forbidden_emotions[emotion]}) over the last {SLIDING_WINDOW_SIZE} frame(s). Average confidence: {FERAvg[emotion]}")
                    signal.raise_signal(signal.SIGTERM)
                    sys.exit(signal.SIGTERM)

            print(f"[calcForbidden] no forbidden emotions met trigger threshold over the last {SLIDING_WINDOW_SIZE} frame(s).")
    except Exception as e:
        traceback.print_exc()
        print(e)
        signal.raise_signal(signal.SIGTERM)
        sys.exit(signal.SIGTERM)

def observerFunction(known_faces,forbidden_emotions=[],min_confidence=50,trigger_on_unknown=False,trigger_on_no_faces=False,trigger_on_camera_block=True):
    import numpy
    print("[observerFunction] Finished initialization of modules.")

    #####################################################
    # INITIALIZATION FOR face_recognition               #
    #                                                   #
    #####################################################
    # Load a sample picture and learn how to recognize it.
    
    #for file in known_faces

    # Create arrays of known face encodings and their names    
    frameQueue = multiprocessing.Queue(maxsize=MAX_BUFFER_SIZE)
    faceDetectionQueue = multiprocessing.Queue(maxsize=MAX_BUFFER_SIZE)
    faceVerifResultsQueue = multiprocessing.Queue(maxsize=MAX_BUFFER_SIZE)
    facialEmotionQueue = multiprocessing.Queue(maxsize=MAX_BUFFER_SIZE)
    FERAvgQueue = multiprocessing.Queue(maxsize=MAX_BUFFER_SIZE)

    identityMap = {}

    forbidden_emotions = {
        "anger": 0.0000001
    }

    identityMap[None] = {
        "lock": threading.Lock(),
        "values": {
            "happy":queue.Queue(maxsize=SLIDING_WINDOW_SIZE),
            "neutral":queue.Queue(maxsize=SLIDING_WINDOW_SIZE),
            "surprise":queue.Queue(maxsize=SLIDING_WINDOW_SIZE),
            "sad":queue.Queue(maxsize=SLIDING_WINDOW_SIZE),
            "angry":queue.Queue(maxsize=SLIDING_WINDOW_SIZE),
            "fear":queue.Queue(maxsize=SLIDING_WINDOW_SIZE),
            "disgust":queue.Queue(maxsize=SLIDING_WINDOW_SIZE)
        }
    }

    for entry in os.scandir(known_faces): # filenames
        if (entry.is_file) and (entry.name not in ignoreList):
            identityMap[entry.name] =  {
                "lock": threading.Lock(),
                "values": {
                    "happy":queue.Queue(maxsize=SLIDING_WINDOW_SIZE),
                    "neutral":queue.Queue(maxsize=SLIDING_WINDOW_SIZE),
                    "surprise":queue.Queue(maxsize=SLIDING_WINDOW_SIZE),
                    "sad":queue.Queue(maxsize=SLIDING_WINDOW_SIZE),
                    "angry":queue.Queue(maxsize=SLIDING_WINDOW_SIZE),
                    "fear":queue.Queue(maxsize=SLIDING_WINDOW_SIZE),
                    "disgust":queue.Queue(maxsize=SLIDING_WINDOW_SIZE)
                }
            }
            threading.Thread(target=calculateAverage, args=(identityMap, entry.name, FERAvgQueue), daemon=True).start()

    # Using multiprocessing.Process for true parallelism seems to randomly provide [WinError 6] The handle is invalid.
    # The `multiprocessing` module uses `subprocess` under the hood to bypass Python's Global Interpreter Lock for true multiprocessing capability (effectively by spawning new python processses).
    # https://stackoverflow.com/questions/65805816/getting-oserror-winerror-6-the-handle-is-invalid https://stackoverflow.com/questions/40108816/python-running-as-windows-service-oserror-winerror-6-the-handle-is-invalid
    # These seeem to indicate that the problem is with how the modules handle multiprocess initialisation; the exact cause is unknown but appears to be low-level enough to not be feasibly patchable in the context of the FYP.
    # So unfortunately, they must compete for processing time with the 'heartbeat' thread of ObsProcess.
    # This effectively enforces interleaving on Windows platforms.

    frameProcess = threading.Thread(target=getFrameFromWebcam, args=(frameQueue,), daemon=True)
    faceEnumProcess = threading.Thread(target=enumFacesInFrame, args=(frameQueue, faceDetectionQueue), daemon=True)
    faceVerifProcess = threading.Thread(target=extractFaceAndVerify, args=(faceDetectionQueue, identityMap, faceVerifResultsQueue), daemon=True)
    FERProcess = threading.Thread(target=determineFacialEmotion, args=(faceVerifResultsQueue, facialEmotionQueue), daemon=True)
    identityMapProcess = threading.Thread(target=putEmotionIntoIdentityMap, args=(facialEmotionQueue, identityMap), daemon=True)
    #calcAvg has already happened before.
    calcForbiddenProcess = threading.Thread(target=calcForbidden, args=(FERAvgQueue,forbidden_emotions), daemon=True)


    frameProcess.start()
    faceEnumProcess.start()
    faceVerifProcess.start()
    FERProcess.start()
    identityMapProcess.start()
    #See above for calcAvg
    calcForbiddenProcess.start()

    while True: time.sleep(1)

    #####################################################

    # TODO: convert all OpenCV calls to dlib in order to fix the cropping issue (see below).
    #video_capture = cv2.VideoCapture(0)
    # "Faces by default are detected using OpenCV's Haar Cascade classifier. To use the more accurate MTCNN network, add the parameter:"
    
    #while True:

    #    ret, frame = video_capture.read()
    #    if (ret == False) and trigger_on_camera_block: 
    #        signal.raise_signal(signal.SIGTERM)
    #        sys.exit(signal.SIGTERM)

    #    facesDetected = False

        # Extract all faces from the openCV frame. Provide the results only.
    #    try:
    #        face_locations = DeepFace.extract_faces(img_path=frame, detector_backend=DETECTOR_BACKEND)
    #        facesDetected = True
    #        print("Detected faces in frame!")
    #        print(face_locations)
    #    except ValueError:
    #        print("No faces detected in frame.")
    #        if trigger_on_no_faces: 
    #            signal.raise_signal(signal.SIGTERM)
    #            sys.exit(signal.SIGTERM)
    #        pass
        
    #    if facesDetected:
    #        for face_result in face_locations:
    #            
    #            # If our attempt to match the face to a known database fails and we've set to trigger:
    #            for identity in identityMap.keys():
    #                faceFound = DeepFace.verify(
    #                    img1_path=frame[
    #                        face_result['facial_area']['y']:face_result['facial_area']['y']+face_result['facial_area']['h'],
    #                        face_result['facial_area']['x']:face_result['facial_area']['x']+face_result['facial_area']['w']
    #                    ], 
    #                    img2_path=f"{known_faces}/{identity}",
    #                    silent=True,
    #                    enforce_detection=False,
    #                    detector_backend=DETECTOR_BACKEND
    #                )['verified']

    #                if faceFound: 
                    #faces_recognized = DeepFace.find(img_path=face_result['face'], db_path=known_faces, silent=True)
                        #cv2.imwrite(
                        #    f"detected_frames/authorized/{identity}",
                        #    frame[
                        #        face_result['facial_area']['y']:face_result['facial_area']['y']+face_result['facial_area']['h'],
                        #        face_result['facial_area']['x']:face_result['facial_area']['x']+face_result['facial_area']['w']
                        #    ]
                        #)
                    # Pseudocode description for me to try and wrap my head around the spaghetti here:
                        # 1. Get all of the faces you can recognize in the frame given.
                        # 2. For each face you recognize:
                            # 2.a Analyze what emotion it has.
                            # 2.b Check the 'emotion buffer':
                                # - If it's full, pull the last emotion you had off the queue.
                            # 2.c Put the new dominant emotion and its score onto the queue.
                            # 2.d Is the 'emotion buffer' still full after this?
                                # - If so, time to parse:
                                    # 3.d.1 Map each forbidden emotion in our runtime list into a dictionary, where the value of each key (emotion) is a list.
                                    # 3.d.2 While the queue still has emotions inside of it:
                                        # 3.d.2.a Get an entry off the queue.
                                            # 3.d.2.a.1 Is the emotion forbidden?
                                                # add it to the forbidden_emotion map's list of values.
                                    # 3.d.3 For each emotion in the forbidden emotion map:
                                        # 3.d.3.a Change the value of the list into the sum of the emotions inside.
                                        # 3.d.3.b If the sum of any one emotion is greater than the minimum score, trigger.

                        #for person in faces_recognized:
    #                    analysis_results = DeepFace.analyze(
    #                        img_path=frame[
    #                            face_result['facial_area']['y']:face_result['facial_area']['y']+face_result['facial_area']['h'],
    #                            face_result['facial_area']['x']:face_result['facial_area']['x']+face_result['facial_area']['w']
    #                        ],
    #                        actions=["emotion"],
    #                        silent=True,
    #                       enforce_detection=False,
    #                        detector_backend=DETECTOR_BACKEND
    #                    )

    #                    if len(analysis_results) != 1: raise Exception("More than one face was detected in the cropped image...")
                    
    #                    emotion_results = analysis_results[0] # We're cropping straight to the detected face, so we should only ever have one entry.

    #                    print(f"Identity: {identity}\nDominant emotion: {emotion_results['dominant_emotion']}\nScore: {emotion_results['emotion'][emotion_results['dominant_emotion']]}")
                            
    #                    if identityMap[identity].full(): 
    #                        identityMap[identity].get_nowait() # Cycle the last one out if full.

    #                    emotionMap = dict()
    #                    for emotion in emotion_results['emotion'].keys():
    #                        if emotion in forbidden_emotions:
    #                            emotionMap[emotion] = emotion_results['emotion'][emotion]
                        
    #                    identityMap[identity].put(emotionMap)

    #                    print(emotionMap)
    #                    print(identityMap[identity].qsize())

    #                    if identityMap[identity].full(): # If we've parsed 5 frames
    #                        forbidden_emotions_map = {}
    #                        for emotion in forbidden_emotions:
    #                            forbidden_emotions_map[emotion] = []


    #                        while not identityMap[identity].empty():
    #                            lastEmotion = identityMap[identity].get()
    #                            for emotion in lastEmotion:
    #                                forbidden_emotions_map[emotion].append(lastEmotion[emotion])
                            
    #                        strongestEmotion = None
    #                        strongestAvgConfidence = 0

    #                        print(forbidden_emotions_map)

    #                        for emotion in forbidden_emotions:
    #                            forbidden_emotions_map[emotion] = sum(forbidden_emotions_map[emotion])/len(forbidden_emotions_map[emotion])
    #                            if forbidden_emotions_map[emotion] > strongestAvgConfidence: 
    #                               strongestAvgConfidence = forbidden_emotions_map[emotion]
    #                                strongestEmotion = emotion

    #                        print(forbidden_emotions_map)

    #                        if strongestAvgConfidence >= min_confidence:
    #                            print(f"Forbidden emotion ({strongestEmotion}) exceeded minimum confidence ({min_confidence}) over {MAX_BUFFER_SIZE} frame(s). Average confidence: {strongestAvgConfidence}")
    #                            signal.raise_signal(signal.SIGTERM)
    #                            sys.exit(signal.SIGTERM)

    #                else: # This is an unknown person
    #                    if trigger_on_unknown: 
    #                        signal.raise_signal(signal.SIGTERM)
    #                        sys.exit(signal.SIGTERM)
    #                    pass

                #except ValueError as e:
                #    print(e)
                #    print("Person could not be recognized")
                #    cv2.imwrite("detected_frames/unknown/detected.jpg",
                #        frame[
                #            face_result['facial_area']['y']:face_result['facial_area']['y']+face_result['facial_area']['h'],
                #            face_result['facial_area']['x']:face_result['facial_area']['x']+face_result['facial_area']['w']
                #        ]
                #    )
                #    if trigger_on_unknown: print("Would've died here...") #signal.raise_signal(signal.SIGTERM); sys.exit(signal.SIGTERM)

if __name__ == "__main__":

    obsProcess(
        args.host,
        args.port,
        func=observerFunction,
        args=(list(args.known_faces)) # Monkey patch while I figure out why passing things to args isn't working in the internal class.
    )
    