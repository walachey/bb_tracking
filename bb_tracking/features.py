import datetime
import math
import numpy as np
import numba
from . import types
from .training import detection_data_generation
import scipy.spatial.distance

def detection_temporal_distance(detection0, detection1):
    return detection1.timestamp_posix - detection0.timestamp_posix

@numba.njit
def euclidean_distance(x0, y0, x1, y1):
    return np.sqrt((x1 - x0) ** 2.0 + (y1 - y0) ** 2.0)

def detection_distance(detection0, detection1, norm=np.nan):
    if np.isnan(norm):
        norm = detection_temporal_distance(detection0, detection1)
    return euclidean_distance(detection0.x_hive, detection0.y_hive, detection1.x_hive, detection1.y_hive) \
            / norm

@numba.njit
def temporal_distance_vectorized_(t0, t1, matrix):
    for i in range(t0.shape[0]):
        for j in range(t1.shape[0]):
            matrix[i, j] = t1[j, 0] - t0[i, 0]

def temporal_distance_vectorized(t0, t1):
    """Takes two column vectors t0, t1 and returns t1[j] - t0[i] in a cdist-like matrix.
    Faster than scipy.spatial.cdist.
    """
    m = np.zeros(shape=[t0.shape[0], t1.shape[0]], dtype=np.float32)
    temporal_distance_vectorized_(t0, t1, m)
    return m

def detection_raw_distance_vectorized(detections_left, detections_right, norm=None):
    coordinates_left = np.nan * np.zeros(shape=(len(detections_left), 3))
    for idx, detection in enumerate(detections_left):
        coordinates_left[idx] = detection.x_hive, detection.y_hive, detection.timestamp_posix

    coordinates_right = np.nan * np.zeros(shape=(len(detections_right), 3))
    for idx, detection in enumerate(detections_right):
        coordinates_right[idx] = detection.x_hive, detection.y_hive, detection.timestamp_posix
    distances = scipy.spatial.distance.cdist(coordinates_left[:, :2], coordinates_right[:, :2])
    temporal_distances = temporal_distance_vectorized(coordinates_left[:, 2:3], coordinates_right[:, 2:3])
    return distances, temporal_distances

@numba.njit
def calculate_forward_motion(x0, y0, a0, x1, y1, a1, norm):
    motion_direction = np.array([x1 - x0,
                                 y1 - y0]) / norm
    a0 = np.array([np.sin(a0), np.cos(a0)])
    a1 = np.array([np.sin(a1), np.cos(a1)])
    
    d0, d1 = np.dot(a0, motion_direction), np.dot(a1, motion_direction)
    if np.isnan(d0) and np.isnan(d1):
        return np.nan
    lowest_d = np.nanmin((d0, d1))
    return lowest_d

def detection_forward_motion(detection0, detection1, norm=np.nan):
    if np.isnan(norm):
        norm = detection_temporal_distance(detection0, detection1)
    return calculate_forward_motion(detection0.x_hive, detection0.y_hive, detection0.orientation_hive,
                                    detection1.x_hive, detection1.y_hive, detection1.orientation_hive,
                                    norm)

@numba.njit
def angular_distance(a0, a1, norm):
    diff = abs((a0 - a1) % (2.0 * math.pi))
    if diff >= math.pi:
        diff -= 2.0 * math.pi
    return abs(diff) / norm

def detection_angular_distance(detection0, detection1, norm=np.nan):
    if np.isnan(norm):
        norm = detection_temporal_distance(detection0, detection1)
    a0, a1 = detection0.orientation_hive, detection1.orientation_hive
    return angular_distance(a0, a1, norm)

def bitwise_distance(bits0, bits1, fun):
    if bits0 is None and bits1 is None:
        return np.nan
    elif bits0 is None or bits1 is None:
        return np.nan
    assert not np.any(np.isnan(bits0))
    assert not np.any(np.isnan(bits1))
    return fun(np.abs(np.array(bits0) - np.array(bits1)))

def bitwise_manhattan_distance(bits0, bits1):
    return bitwise_distance(bits0, bits1, np.mean)

def detection_id_distance(detection0, detection1):
    bits0, bits1 = detection0.bit_probabilities, detection1.bit_probabilities
    return bitwise_manhattan_distance(bits0, bits1)

def detection_type_to_index(t):
    if t == types.DetectionType.TaggedBee:
        return 0
    if t == types.DetectionType.UntaggedBee:
        return 1
    if t == types.DetectionType.BeeOnGlass:
        return 2
    if t == types.DetectionType.BeeInCell:
        return 3
    assert False

def get_detection_confidence(detection):
    if detection.bit_probabilities is None:
        return np.nan
    return 2.0 * np.mean(np.abs(detection.bit_probabilities - 0.5))

def detection_confidences(detection0, detection1):
    return (get_detection_confidence(detection0),
            get_detection_confidence(detection1))

def detection_localizer_saliencies(detection0, detection1):
    return (detection0.localizer_saliency,
            detection1.localizer_saliency)

def detection_track_type_changes_mask(tracklet0, detection1):
    detections = tracklet0.detections[-5:], [detection1]
    values = [0, 0, 0, 0, 0, 0, 0, 0]
    for track_idx in range(2):
        for det in detections[track_idx]:
            values[((4 * track_idx) + detection_type_to_index(det.detection_type))] += 1
        for i in range(4):
            values[4 * track_idx + i] /= len(detections[track_idx])
    return tuple(values)

def get_detection_features(tracklet0, detection1):
    detection0 = tracklet0.detections[-1]
    return (
            detection_distance(detection0, detection1),
            detection_angular_distance(detection0, detection1),
            detection_id_distance(detection0, detection1),
            detection_forward_motion(detection0, detection1),
            track_forward_distance(tracklet0, detection1),
            float(detection_data_generation.is_valid_detection_pair_combination(detection0, detection1)),
            ) + \
                detection_confidences(detection0, detection1) + \
                detection_localizer_saliencies(detection0, detection1) + \
                detection_track_type_changes_mask(tracklet0, detection1)

def detection_id_match(detection0, detection1):
    bits0, bits1 = detection0.bit_probabilities, detection1.bit_probabilities
    if bits0 is None and bits1 is None:
        return 1.0
    elif bits0 is None or bits1 is None:
        return 0.0
    
    if ((bits0 > 0.5) ^ (bits1 > 0.5)).sum() > 0:
        return 0.0
    return 1.0

    max_bit_distance = bitwise_distance(detection0.bit_probabilities, detection1.bit_probabilities, np.max)
    if max_bit_distance < 0.5:
        return 1.0
    return 0.0

def detection_id_match_cost(detection0, detection1):
    return 1.0 - detection_id_match(detection0, detection1)

def get_detection_features_id_only(detection0, detection1):
    return (detection_id_match(detection0, detection1),)

def track_median_id(tracklet):
    if "track_median_id" in tracklet.cache_:
        return tracklet.cache_["track_median_id"]

    tracklet_bits = np.array([d.bit_probabilities for d in tracklet.detections if d.detection_type == types.DetectionType.TaggedBee])
    assert not np.any(np.isnan(tracklet_bits))
    if tracklet_bits.shape[0] == 0:
        mean_id = None
    else:
        mean_id = np.median(tracklet_bits, axis=0)

    tracklet.cache_["track_median_id"] = mean_id
    return mean_id

def track_stable_id(tracklet):
    if "track_stable_id" in tracklet.cache_:
        return tracklet.cache_["track_stable_id"]

    tracklet_bits = np.array([d.bit_probabilities for d in tracklet.detections if d.detection_type == types.DetectionType.TaggedBee])
    assert not np.any(np.isnan(tracklet_bits))
    if tracklet_bits.shape[0] == 0:
        track_id = None
    else:
        bit_confidences = np.abs(tracklet_bits - 0.5) * 2.0
        confidences = np.mean(np.log1p(bit_confidences), axis=1)
        N = max(5, tracklet_bits.shape[0] // 10)
        confidences_idx = np.argsort(confidences)[::-1][:N]
        good_bits = tracklet_bits[confidences_idx, :]
        track_id = np.median(good_bits, axis=0)

    tracklet.cache_["track_stable_id"] = track_id
    return track_id

def track_median_id_distance(tracklet0, tracklet1):
    return bitwise_manhattan_distance(track_median_id(tracklet0), track_median_id(tracklet1))

def track_stable_id_distance(tracklet0, tracklet1):
    return bitwise_manhattan_distance(track_stable_id(tracklet0), track_stable_id(tracklet1))

def track_distance(tracklet0, tracklet1, norm=np.nan):
    return detection_distance(tracklet0.detections[-1], tracklet1.detections[0], norm=norm)

@numba.njit
def short_angle_dist(a0,a1):
    """Returns the signed distance between two angles in radians.
    """
    max = np.pi*2
    da = (a1 - a0) % max
    return 2*da % max - da

@numba.njit
def extrapolate_position_and_angles(x0, y0, a0, x1, y1, a1, factor):
    x = x1 + (x1 - x0) * factor
    y = y1 + (y1 - y0) * factor
    a = a1 + short_angle_dist(a0, a1) * factor
    return x, y, a


def extrapolate_detections(seconds, *detections):
    if len(detections) == 1:
        return detections[0]

    seconds_distance = detection_temporal_distance(*detections)
    extrapolation_factor = seconds / seconds_distance

    x, y, orientation = extrapolate_position_and_angles(detections[0].x_hive, detections[0].y_hive, detections[0].orientation_hive,
                                             detections[1].x_hive, detections[1].y_hive, detections[1].orientation_hive,
                                             extrapolation_factor)

    return types.Detection(None, None, None,
            x, y, orientation,
            None, detections[1].timestamp_posix + seconds, 0,
            detections[1].detection_type, None, None, None)

def track_forward_distance(tracklet0, detection1, seconds_distance=np.nan):
    if np.isnan(seconds_distance):
        seconds_distance = detection_temporal_distance(tracklet0.detections[-1], detection1)
    return detection_distance(extrapolate_detections(seconds_distance, *tracklet0.detections[-2:]), detection1, norm=1.0)

def track_decoder_confidence(tracklet):
    if "track_decoder_confidence" in tracklet.cache_:
        return tracklet.cache_["track_decoder_confidence"]

    bits = [d.bit_probabilities for d in tracklet.detections if d.detection_type == types.DetectionType.TaggedBee]
    if len(bits) != 0:
        bits = np.median(np.array(bits), axis=0)
        confidence = np.min(np.abs(bits - 0.5))
    else:
        confidence = 0.0
    tracklet.cache_["track_decoder_confidence"] = confidence
    return confidence

def track_difference_of_confidence(tracklet0, tracklet1):
    return abs(track_decoder_confidence(tracklet0) - track_decoder_confidence(tracklet1))

def track_type_changes_mask(tracklet0, tracklet1):
    detections = tracklet0.detections[-5:], tracklet1.detections[:5]
    values = [0, 0, 0, 0, 0, 0, 0, 0]
    for track_idx in range(2):
        for det in detections[track_idx]:
            values[((4 * track_idx) + detection_type_to_index(det.detection_type))] += 1
        for i in range(4):
            values[4 * track_idx + i] /= len(detections[track_idx])
    return tuple(values)

def get_track_features(tracklet0, tracklet1):
    seconds_distance = detection_temporal_distance(tracklet0.detections[-1], tracklet1.detections[0])
    raw_track_distance = track_distance(tracklet0, tracklet1, norm=1.0)

    return (
            float(detection_data_generation.is_valid_detection_pair_combination(tracklet0.detections[-1], tracklet1.detections[0])),
            raw_track_distance,
            track_median_id_distance(tracklet0, tracklet1),
            track_stable_id_distance(tracklet0, tracklet1),
            detection_id_distance(tracklet0.detections[-1], tracklet1.detections[0]),
            track_difference_of_confidence(tracklet0, tracklet1)) + \
                track_type_changes_mask(tracklet0, tracklet1)

def track_id_match_cost(tracklet0, tracklet1):
    return detection_id_match_cost(tracklet0.detections[-1], tracklet1.detections[0])
