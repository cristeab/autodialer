#!/usr/bin/env python

"""Google Cloud Speech API application using the REST API for async
batch processing.
"""

import io
import os
import logging as log


def transcribe_file(wave_file, dest_num, lang='en-US'):
    """Transcribe the given audio file asynchronously."""
    from google.cloud import speech
    from google.cloud.speech import enums
    from google.cloud.speech import types

    try:

        if not wave_file:
            log.critical('No wave file provided for transcription')
            return
        log.info('Transcribing wave file %s', wave_file)

        client = speech.SpeechClient()

        with io.open(wave_file, 'rb') as audio_file:
            content = audio_file.read()

        audio = types.RecognitionAudio(content=content)
        config = types.RecognitionConfig(
            encoding=enums.RecognitionConfig.AudioEncoding.LINEAR16,
            language_code=lang)

        operation = client.long_running_recognize(config, audio)

        log.debug('Waiting for transcript operation to complete...')
        result = operation.result(timeout=90)
        log.debug('Transcript operation finished')

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
        log.warning('Cannot transcribe file: %s', e.message)

