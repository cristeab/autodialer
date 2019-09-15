#!/usr/bin/env python2

import sys
import os
import glob
import subprocess
import io

GOOGLE_API_CREDENTIALS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sage-collector-250907-e0be5d8f9867.json')


def transcribe_file(wave_file, dest_num, lang='en-US'):
    """Transcribe the given audio file asynchronously."""
    from google.cloud import speech
    from google.cloud.speech import enums
    from google.cloud.speech import types

    try:

        if not wave_file:
            print('No wave file provided for transcription')
            return
        # print('Transcribing ', wave_file)

        client = speech.SpeechClient()

        with io.open(wave_file, 'rb') as audio_file:
            content = audio_file.read()

        audio = types.RecognitionAudio(content=content)
        config = types.RecognitionConfig(
            encoding=enums.RecognitionConfig.AudioEncoding.LINEAR16,
            language_code=lang)

        # synchronous transcription
        result = client.recognize(config, audio)

        # prepare filename for the transcript (we need to use a temporary separate file for each call)
        transcript_filename = wave_file+'.txt'

        # write into file destination number, converted text and confidence interval
        with open(transcript_filename, 'w', os.O_NONBLOCK) as transcript_file:
            transcript_file.write('Destination Number: {}\n'.format(dest_num))
            no_transcript = True
            if result.results:
                alternatives = result.results[0].alternatives
                if alternatives:
                    no_transcript = False
                    for alternative in alternatives:
                        transcript_file.write('Transcript: {}\n'.format(alternative.transcript))
                        transcript_file.write('Confidence: {}\n'.format(alternative.confidence))
            if no_transcript:
                transcript_file.write('No transcript available\n\n')
            else:
                transcript_file.write('\n')

    except Exception as e:
        print('Cannot transcribe file')


def print_progress(current, total):
    percent = 100*float(current)/total
    sys.stdout.write("\r%d%%" % percent)
    sys.stdout.flush()


def process_folder(path):
    print('Convert all found amr files to wav')
    amr_path = os.path.join(path, '*.amr')
    for amr_filename in glob.glob(amr_path):
        wav_filename = os.path.splitext(amr_filename)[0] + '.wav'
        subprocess.call(['sox', amr_filename, wav_filename])
    print('Transcribe existing wav files')
    wave_path = os.path.join(path, '*.wav')
    wave_files_num = len(glob.glob(wave_path))
    count = 0
    for wave_filename in glob.glob(wave_path):
        print_progress(count, wave_files_num)
        count += 1
        dest_num = os.path.splitext(wave_filename)[0]
        transcribe_file(wave_filename, dest_num)
    print('Merge all transcriptions into a single text file')
    transcript_filename = os.path.join(path, 'transcripts.txt')
    with open(transcript_filename, 'w', os.O_NONBLOCK) as transcript_file:
        for wave_filename in glob.glob(wave_path):
            temp_transcript_filename = wave_filename + '.txt'
            if os.path.isfile(temp_transcript_filename):
                with open(temp_transcript_filename, 'r', os.O_NONBLOCK) as temp_transcript_file:
                    for line in temp_transcript_file:
                        transcript_file.write(line)
                os.remove(temp_transcript_filename)
            os.remove(wave_filename)


if __name__ == '__main__':
    if 1 < len(sys.argv):
        path = sys.argv[1]
        if os.path.isdir(path):
            # set environment variable for Google Cloud Speech API application
            os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = GOOGLE_API_CREDENTIALS
            # process folder
            process_folder(path)
        else:
            print('The provided path', path, 'is not a folder')
    else:
        print('This script should be called with the path of the audio files to translate as argument')
        print('The provided folder must contain either amr or wav files')
        print('The output is a text file, transcripts.txt, with all transcripts')
        print(sys.argv[0], ' <audio files path>')


