import os
import glob
import json
import logging
import pickle
import zipfile
import multiprocessing

import numpy as np
import scipy.signal
import mne
import pylab as pl
import pycmdstan.io

logger = logging.getLogger('preprocess_all.py')

patterns = {
    'seeg_fif_json': 'seeg fif *.json',
    'elec_gain_destriux': 'elec gain_inv-square.destrieux.txt',
    'seeg_xyz': 'elec seeg.xyz',
    'weights': 'tvb connectivity.destrieux.zip'
}


def build_pattern(root: str, pattern: str) -> str:
    parts = root.split(' ') + ['*'] + pattern.split(' ')
    return os.path.join(*parts)


def list_files(root: str, pattern_name: str) -> [os.PathLike]:
    pattern = build_pattern(root, patterns[pattern_name])
    logger.debug(f'list_files pattern {pattern}')
    return glob.glob(pattern)


def patient_id_from_path(root: str, path: str) -> str:
    logger.debug(f'root={root}, path={path}')
    _, rest = path.split(os.path.join(*root.split(' ')))
    _, patient_id, *_ = rest.split(os.path.sep)
    return patient_id


def files_per_patient(root: str) -> dict:
    js_found = list_files(root, 'seeg_fif_json')
    gain_found = list_files(root, 'elec_gain_destriux')
    seeg_xyz_found = list_files(root, 'seeg_xyz')
    weights_found = list_files(root, 'weights')
    per_patient = {}
    for js in js_found:
        pid = patient_id_from_path(root, js)
        if pid not in per_patient:
            per_patient[pid] = {}
        if 'fifs' not in per_patient[pid]:
            per_patient[pid]['fifs'] = []
        per_patient[pid]['fifs'].append(js)
    for gain in gain_found:
        pid = patient_id_from_path(root, gain)
        per_patient[pid]['gain'] = gain
    for seeg_xyz in seeg_xyz_found:
        pid = patient_id_from_path(root, seeg_xyz)
        per_patient[pid]['seeg_xyz'] = seeg_xyz
    for weights in weights_found:
        pid = patient_id_from_path(root, weights)
        per_patient[pid]['weights'] = weights
    return per_patient


def process_patient_gain(pid, gain_fname):
    gain = np.loadtxt(gain_fname)
    gain_ps = np.percentile(gain.flat[:], [90, 95, 99])
    logger.debug(f'process_patient {pid} gain.shape = {gain.shape}')
    logger.debug(f'process_patient {pid} gain 90, 95, 99% = {gain_ps}')
    return gain


def process_fif_js(pid, fif_js_fname):
    logger.debug('%s', fif_js_fname)
    with open(fif_js_fname, 'r') as fd:
        js = json.load(fd)
    exclude = js['bad_channels'] + js['non_seeg_channels']
    fif_fname = os.path.join(os.path.dirname(fif_js_fname), js['filename'])
    raw = mne.io.Raw(fif_fname)
    logger.debug('%s', raw)
    # need this to reduce gain as well
    picks = set(raw.ch_names) - set(exclude)
    if js['onset'] is not None and js['termination'] is not None:
        raw.crop(tmin=js['onset'], tmax=js['termination'])
    raw.load_data()
    raw.pick_channels(picks)
    slp = compute_raw_slp(raw)
    return picks, slp, raw.ch_names


def process_patient_seeg_xyz(fname):
    lines = []
    with open(fname, 'r') as fd:
        for line in fd.readlines():
            lines.append(line.strip().split())
    return lines


def process_patient_weights(fname):
    roi_names = []
    with zipfile.ZipFile(fname) as zf:
        with zf.open('weights.txt') as fd:
            weights = np.loadtxt(fd)
        with zf.open('centres.txt', 'r') as fd:
            for line in fd.readlines():
                roi_name, *_ = line.decode('ascii').strip().split(' ')
                roi_names.append(roi_name)
    weights_triu = weights[np.triu_indices(weights.shape[0], 1)]
    return weights, weights_triu, roi_names


def process_patient(pid, files):
    weights, weights_triu, roi_names = process_patient_weights(
        files['weights'])
    gain = process_patient_gain(pid, files['gain'])
    seeg_xyz = process_patient_seeg_xyz(files['seeg_xyz'])
    fif_fname = files['fif']
    logger.info(f'processing {fif_fname}')
    picks, slp, ch_names = process_fif_js(pid, fif_fname)
    gain_pick = np.array(
        [i for i, (label, *_) in enumerate(seeg_xyz) if label in picks])
    pl.figure(figsize=(15, 15))
    pl.subplot(121)
    pl.imshow(slp, vmin=6, vmax=12)
    pl.subplot(222)
    pl.imshow(gain)
    pl.subplot(224)
    pl.imshow(np.log(weights))
    pl.savefig(f'datasets/{pid}.png')
    pl.close()
    dataset = {
        'pid': pid,
        'slp': slp,
        'picks': picks,
        'gain': gain[gain_pick],
        'weights': weights_triu,
        'nn': gain.shape[1],
        'ns': len(gain_pick),
        'nt': slp.shape[1],
        'ch_names': ch_names,
        'roi_names': roi_names,
    }
    pycmdstan.io.rdump(f'datasets/{pid}.R',
                       {k: v
                        for k, v in dataset.items() if k != 'pid'})
    return dataset


def compute_raw_slp(raw, nperseg=4096, cutoff=20.0):
    Cs = []
    for y in raw._data:
        F, T, C = scipy.signal.spectrogram(
            y, raw.info['sfreq'], nperseg=nperseg)
        Cs.append(np.log(C[F > cutoff].sum(axis=0)))
    Cs = np.array(Cs)
    return Cs


def _process_patient_mp_helper(item):
    k, v = item
    try:
        return process_patient(k, v)
    except Exception as e:
        logger.exception(e, k, v)
        return {}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    root = r'E:\ duke RetrospectivePatients 1-Processed'
    logger.info('finding files')
    files = files_per_patient(root)
    files_ = {}
    for k, v in files.items():
        for i, fif in enumerate(v['fifs']):
            files_[f'{k}_{i}'] = {
                k_: v_
                for k_, v_ in v.items() if k_ != 'fifs'
            }
            files_[f'{k}_{i}']['fif'] = fif
    logger.info('processing all files')
    with multiprocessing.Pool(8) as pool:
        datasets = pool.map(_process_patient_mp_helper, files_.items())
    logger.info('writing data')
    with open('datasets.pickle', 'wb') as fd:
        pickle.dump(datasets, fd)