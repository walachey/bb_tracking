from .. import data_walker
from .. import features
from .. import types

import concurrent.futures
import datetime
import pytz
import tqdm.auto

def is_valid_detection_pair_combination(det, next_det):
    """
    The GT data contains some odd combinations that, while being present in the real data due do pipeline oddities,
    are not helpful for the models, leading to more false positives.
    """
    # Skip tag->no tag or no tag->tag transitions.
    if (((det.detection_type == types.DetectionType.TaggedBee) and (next_det.detection_type == types.DetectionType.UntaggedBee)) or
        ((det.detection_type == types.DetectionType.UntaggedBee) and (next_det.detection_type == types.DetectionType.TaggedBee))):
        return False
    # Same for glass<->cell transitions.
    if (((det.detection_type == types.DetectionType.BeeOnGlass) and (next_det.detection_type == types.DetectionType.BeeInCell)) or
        ((det.detection_type == types.DetectionType.BeeInCell) and (next_det.detection_type == types.DetectionType.BeeOnGlass))):
        return False
    # Same for glass<->tag transitions. We don't have as much data for that as we would need for a good model.
    if (((det.detection_type == types.DetectionType.BeeOnGlass) and (next_det.detection_type == types.DetectionType.TaggedBee)) or
        ((det.detection_type == types.DetectionType.TaggedBee) and (next_det.detection_type == types.DetectionType.BeeOnGlass))):
        return False
    return True

def are_same_detections(d0, d1):
    return d0.frame_id == d1.frame_id \
        and d0.detection_type == d1.detection_type \
        and d0.detection_index == d1.detection_index

def generate_detection_features_for_frame(tracklet, follow_up_detection, candidate_detections, distance_per_second_hard_limit):

    detection = tracklet.detections[-1]

    results = []
    
    for i in range(2):
        if i == 1:
            tracklet = tracklet._replace(
                                detections=tracklet.detections[-1:],
                                timestamps=tracklet.timestamps[-1:],
                                frame_ids=tracklet.frame_ids[-1:])

        if follow_up_detection is not None:
            detection_features = features.get_detection_features(tracklet, follow_up_detection)
            results.append((detection_features, 1, (detection.frame_id, detection.detection_index, detection.x_pixels, detection.y_pixels,
                                                    follow_up_detection.detection_index, follow_up_detection.x_pixels, follow_up_detection.y_pixels)))

        for candidate_detection in candidate_detections:
            if follow_up_detection is not None and are_same_detections(candidate_detection, follow_up_detection):
                continue
            detection_features = features.get_detection_features(tracklet, candidate_detection)
            # Check distance hard limit.
            if detection_features[0] > distance_per_second_hard_limit:
                continue
            results.append((detection_features, 0, (detection.frame_id, detection.detection_index, detection.x_pixels, detection.y_pixels,
                                                    candidate_detection.detection_index, candidate_detection.x_pixels, candidate_detection.y_pixels)))
    
    return results

def generate_detection_features(gt_tracks, repository_path, cam_id, homography_fn, distance_per_second_hard_limit=30.0):
    timestamp_minmax = None
 
    for track in gt_tracks:
        track_minmax = min(track.timestamps), max(track.timestamps)
        if timestamp_minmax is None:
            timestamp_minmax = track_minmax
        else:
            timestamp_minmax = min(timestamp_minmax[0], track_minmax[0]), max(timestamp_minmax[1], track_minmax[1])
            
    print("From {} to {}, cam ID {}.".format(
        timestamp_minmax[0].isoformat(),
        timestamp_minmax[1].isoformat(),
        cam_id))
        
    next_frame_dict = dict()
    last_frame_id = None
    
    def walk_all_frames():
        yield from enumerate(
                    data_walker.iterate_bb_binary_repository(repository_path,
                                timestamp_minmax[0] - datetime.timedelta(seconds=0.01),
                                timestamp_minmax[1] + datetime.timedelta(seconds=0.01), cam_id=cam_id,
                                homography_fn=homography_fn))
    
    for frame_index, (cam_id, frame_id, frame_datetime, frame_detections, _) in walk_all_frames():
        if last_frame_id is not None:
            next_frame_dict[last_frame_id] = frame_id
        last_frame_id = frame_id
    n_frames = len(next_frame_dict)
    print("Interval contains {}+1 frames.".format(n_frames))
    
    
    follow_up_detection_map = dict()
    det_to_tracklet = dict()

    for track in gt_tracks:
        for det_idx in range(len(track.detections)):
            det = track.detections[det_idx]
            det_track = track._replace(detections=track.detections[:det_idx+1],
                                        timestamps=track.timestamps[:det_idx+1],
                                        frame_ids=track.frame_ids[:det_idx+1])
            detection_key = (det.frame_id, det.detection_type, det.detection_index)
            det_to_tracklet[detection_key] = det_track

            if det_idx == len(track.detections) - 1:
                continue
            next_det = track.detections[det_idx+1]
            if next_frame_dict[det.frame_id] != next_det.frame_id:
                continue
            if not is_valid_detection_pair_combination(det, next_det):
                continue
            follow_up_detection_map[detection_key] = next_det
    print("{} detection pairs available.".format(len(follow_up_detection_map)))
    
    future_results = []
    last_frame_detections = None
    
    import concurrent.futures
    import itertools
    
    with concurrent.futures.ProcessPoolExecutor(max_workers=8) as executor:
        for frame_index, (cam_id, frame_id, frame_datetime, frame_detections, _) in \
                    tqdm.auto.tqdm(walk_all_frames(), total=n_frames+1, desc="Submitting jobs"):
            
            if last_frame_detections is not None:
                for detection in last_frame_detections:

                    # Is a real one?
                    follow_up = None
                    det_key = (detection.frame_id, detection.detection_type, detection.detection_index)
                    if det_key in follow_up_detection_map:
                        follow_up = follow_up_detection_map[det_key]
                    
                    if det_key in det_to_tracklet:
                        det_tracklet = det_to_tracklet[det_key]
                    else:
                        det_tracklet = types.Track(id=0,
                                                    cam_id=0,
                                                    detections=[detection],
                                                    timestamps=[detection.timestamp],
                                                    frame_ids=[frame_id],
                                                    bee_id=None, bee_id_confidence=None, cache_=dict())
                    future_results.append(
                                        executor.submit(generate_detection_features_for_frame,
                                                   det_tracklet, follow_up,
                                                   frame_detections,
												   distance_per_second_hard_limit))

            last_frame_detections = frame_detections
        
        results = []
        for r in tqdm.auto.tqdm(future_results, desc="Retrieving results"):
            results += r.result()
    
    n_positives = sum(f[1] for f in results)
    print("{} samples: positives: {}, negatives: {}".format(len(results), n_positives, len(results) - n_positives))
    
    return results