# pylint: disable=invalid-name,too-many-arguments,too-many-locals
import concurrent
import itertools
import zipfile
import random
import json
import os

import tqdm
import numpy as np

from . import tools


def _read_born_digital_labels_file(labels_filepath, image_folder):
    """Read a labels file and return (filepath, label) tuples.

    Args:
        labels_filepath: Path to labels file
        image_folder: Path to folder containing images
    """
    with open(labels_filepath, encoding='utf-8-sig') as f:
        labels = [l.strip().split(',') for l in f.readlines()]
        labels = [(os.path.join(image_folder,
                                segments[0]), None, ','.join(segments[1:]).strip()[1:-1])
                  for segments in labels]
    return labels


def get_cocotext_recognizer_dataset(split='train',
                                    cache_dir=None,
                                    limit=None,
                                    legible_only=False,
                                    english_only=False,
                                    return_raw_labels=False):
    """Get a list of (filepath, box, word) tuples from the
    COCO-Text dataset.

    Args:
        split: Which split to get (train, val, or trainval)
        limit: Limit the number of files included in the download
        cache_dir: The directory in which to cache the file. The default is
            `~/.keras-ocr`.
        return_raw_labels: Whether to return the raw labels object

    Returns:
        A recognition dataset as a list of (filepath, box, word) tuples.
        If return_raw_labels is True, you will also get a (labels, images_dir)
        tuple containing the raw COCO data and the directory in which you
        can find the images.
    """
    assert split in ['train', 'val', 'trainval'], f'Unsupported split: {split}'
    if cache_dir is None:
        cache_dir = os.path.expanduser(os.path.join('~', '.keras-ocr'))
    main_dir = os.path.join(cache_dir, 'coco-text')
    images_dir = os.path.join(main_dir, 'images')
    labels_zip = tools.download_and_verify(
        url='https://github.com/bgshih/cocotext/releases/download/dl/cocotext.v2.zip',
        cache_dir=main_dir,
        sha256='1444893ce7dbcd8419b2ec9be6beb0dba9cf8a43bf36cab4293d5ba6cecb7fb1')
    with zipfile.ZipFile(labels_zip) as z:
        with z.open('cocotext.v2.json') as f:
            labels = json.loads(f.read())
    selected_ids = [cocoid for cocoid, data in labels['imgs'].items() if data['set'] in split]
    if limit:
        flatten = lambda l: [item for sublist in l for item in sublist]
        selected_ids = selected_ids[:limit]
        labels['imgToAnns'] = {k: v for k, v in labels['imgToAnns'].items() if k in selected_ids}
        labels['imgs'] = {k: v for k, v in labels['imgs'].items() if k in selected_ids}
        anns = set(flatten(list(labels.values())))
        labels['anns'] = {k: v for k, v in labels['anns'].items() if k in anns}
    selected_filenames = [labels['imgs'][cocoid]['file_name'] for cocoid in selected_ids]
    with concurrent.futures.ThreadPoolExecutor() as executor:
        for future in tqdm.tqdm(concurrent.futures.as_completed([
                executor.submit(tools.download_and_verify,
                                url=f'http://images.cocodataset.org/train2014/{filename}',
                                cache_dir=images_dir,
                                verbose=False) for filename in selected_filenames
        ]),
                                total=len(selected_filenames),
                                desc='Downloading images'):
            _ = future.result()
    dataset = []
    for selected_id in selected_ids:
        filepath = os.path.join(images_dir, selected_filenames[selected_ids.index(selected_id)])
        for annIdx in labels['imgToAnns'][selected_id]:
            ann = labels['anns'][str(annIdx)]
            if english_only and ann['language'] != 'english':
                continue
            if legible_only and ann['legibility'] != 'legible':
                continue
            dataset.append((filepath, np.array(ann['mask']).reshape(-1, 2), ann['utf8_string']))
    if return_raw_labels:
        return dataset, (labels, images_dir)
    return dataset


def get_born_digital_recognizer_dataset(split='train', cache_dir=None):
    """Get a list of (filepath, box, word) tuples from the
    BornDigital dataset. This dataset comes pre-cropped so
    `box` is always `None`.

    Args:
        split: Which split to get (train, test, or traintest)
        cache_dir: The directory in which to cache the file. The default is
            `~/.keras-ocr`.

    Returns:
        A recognition dataset as a list of (filepath, box, word) tuples
    """
    data = []
    if cache_dir is None:
        cache_dir = os.path.expanduser(os.path.join('~', '.keras-ocr'))
    main_dir = os.path.join(cache_dir, 'borndigital')
    if split in ['train', 'traintest']:
        train_dir = os.path.join(main_dir, 'train')
        training_zip_path = tools.download_and_verify(
            url=
            'https://storage.googleapis.com/keras-ocr/borndigital/Challenge1_Training_Task3_Images_GT.zip',  # pylint: disable=line-too-long
            cache_dir=main_dir,
            sha256='8ede0639f5a8031d584afd98cee893d1c5275d7f17863afc2cba24b13c932b07')
        with zipfile.ZipFile(training_zip_path) as zfile:
            zfile.extractall(train_dir)
        data.extend(
            _read_born_digital_labels_file(labels_filepath=os.path.join(train_dir, 'gt.txt'),
                                           image_folder=train_dir))
    if split in ['test', 'traintest']:
        test_dir = os.path.join(main_dir, 'test')
        test_zip_path = tools.download_and_verify(
            url=
            'https://storage.googleapis.com/keras-ocr/borndigital/Challenge1_Test_Task3_Images.zip',
            cache_dir=main_dir,
            sha256='8f781b0140fd0bac3750530f0924bce5db3341fd314a2fcbe9e0b6ca409a77f0')
        with zipfile.ZipFile(test_zip_path) as zfile:
            zfile.extractall(test_dir)
        test_gt_path = tools.download_and_verify(
            url='https://storage.googleapis.com/keras-ocr/borndigital/Challenge1_Test_Task3_GT.txt',
            cache_dir=test_dir,
            sha256='fce7f1228b7c4c26a59f13f562085148acf063d6690ce51afc395e0a1aabf8be')
        data.extend(
            _read_born_digital_labels_file(labels_filepath=test_gt_path, image_folder=test_dir))
    return data


def get_recognizer_image_generator(labels, height, width, alphabet, augmenter=None):
    """Generate augmented (image, text) tuples from a list
    of (filepath, box, label) tuples.

    Args:
        labels: A list of (filepath, box, label) tuples
        height: The height of the images to return
        width: The width of the images to return
        alphabet: The alphabet which limits the characters returned
        augmenter: The augmenter to apply to images
    """
    n_with_illegal_characters = sum(any(c not in alphabet for c in text) for _, _, text in labels)
    if n_with_illegal_characters > 0:
        print(f'{n_with_illegal_characters} / {len(labels)} instances have illegal characters.')
    labels = labels.copy()
    for index in itertools.cycle(range(len(labels))):
        if index == 0:
            random.shuffle(labels)
        filepath, box, text = labels[index]
        cval = cval = np.random.randint(low=0, high=255, size=3).astype('uint8')
        if box is not None:
            print(box)
            image = tools.warpBox(image=tools.read(filepath),
                                  box=box.astype('float32'),
                                  target_height=height,
                                  target_width=width,
                                  cval=cval)
        else:
            image = tools.read_and_fit(filepath_or_array=filepath,
                                       width=width,
                                       height=height,
                                       cval=cval)
        text = ''.join([c for c in text if c in alphabet])
        if not text:
            continue
        if augmenter:
            image = augmenter.augment_image(image)
        yield (image, text)