#!/usr/bin/env python2

from flask import Flask, request, render_template, url_for, redirect, session
from flask_socketio import SocketIO, emit
from werkzeug import secure_filename
from core import softphone as sf
from core import jsonparser as jp
import random as rnd
import logging as log
import logging.handlers as hnds
import os
from threading import Timer, Event
import sys
import datetime as dt
import time
import glob


app = Flask(__name__)
app.config['SECRET_KEY'] = '31415pi'
app.config['DOWNLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static/download')
app.config['USERNAME'] = 'admin'
app.config['PASSWORD'] = 'secret'
app.config['GOOGLE_API_CREDENTIALS'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sage-collector-250907-e0be5d8f9867.json')
socketio = SocketIO(app, logger=False, engineio_logger=False, async_mode=None)
parser = jp.JsonParser(os.path.join(app.config['DOWNLOAD_FOLDER'], 'providers.json'))

data = {}
destno_list = None
cid_list = None
already_logged = False
currentProviderId = ''
stopped = False
start_timer = None
update_stats_thread = None
stop_event = Event()
log_to_file = False
cur_save_path = ''


def convert_wav2amr(wav_filename):
    try:
        import subprocess
        # convert to amr
        amr_filename = os.path.splitext(wav_filename)[0]+'.amr-nb'
        subprocess.call(['sox', wav_filename, amr_filename])

        # rename file and remove the original wave file
        new_filename = os.path.splitext(wav_filename)[0]+'.amr'
        os.rename(amr_filename, new_filename)
        os.remove(wav_filename)
        log.debug('Converted %s to AMR', wav_filename)
    except:
        log.critical('Cannot convert %s to AMR', wav_filename)


def update_stats():
    while True:
        log.debug('Start sending stats')
        while not stop_event.is_set():
            # update stats periodically
            data['pending'] = sf.pending_calls()
            data['answered'] = sf.answered_calls()
            data['abandoned'] = sf.abandoned_calls()
            data['rejected'] = sf.rejected_calls()
            data['total'] = sf.total_calls()
            socketio.emit('stats',
                          {'pending': data['pending'], 'answered': data['answered'], 'abandoned': data['abandoned'],
                           'rejected': data['rejected'], 'total': data['total']})
            if 0 == data['pending']:
                log.info('No more pending calls')
                # force exit loop
                stop_event.set()
            socketio.sleep(1)
        log.debug('Received stop event')
        socketio.emit('stopped')  # will emulate a click on Stop button
        # wait until the stop event is cleared
        while stop_event.is_set():
            socketio.sleep(1)


def on_start(concurrentCalls, callDuration, phoneNumberPrefix):
    global destno_list
    global cid_list
    global stopped
    global data
    global cur_save_path

    sf.thread_register('on_start')

    # create folder where files for the current session are stored
    cur_folder = dt.datetime.now().strftime('%d_%m_%Y-%H_%M_%S')
    cur_save_path = os.path.join(app.config['DOWNLOAD_FOLDER'], cur_folder)
    if not os.path.exists(cur_save_path):
        os.makedirs(cur_save_path)

    # start concurrent calls
    destNoLen = len(destno_list)
    sf.set_pending_calls(destNoLen)
    destNoIndex = 0
    callsToStart = concurrentCalls
    cliLen = len(cid_list)
    cliIndex = 0
    while not stopped:
        for n in range(0, callsToStart):
            if phoneNumberPrefix:
                dest = phoneNumberPrefix + destno_list[destNoIndex]
            else:
                dest = destno_list[destNoIndex]
            cli = ''
            if cid_list:
                cli = cid_list[cliIndex]
                cliIndex = (cliIndex + 1) % cliLen
            log.info('Starting call to "%s" using caller ID "%s"', dest, cli)
            rc = sf.call(dest, cli, cur_save_path, callDuration)
            if not rc:
                log.critical('Cannot make call')
                stopped = True
                sf.set_pending_calls(0)  # make sure that the UI stops
                break
            destNoIndex += 1
            if destNoIndex >= destNoLen:
                stopped = True
                break  # leave inner loop in case all calls have been started
        # wait for some calls to finish
        callsToStart = 0
        while not stopped and 1 > callsToStart:
            socketio.sleep(1)
            callsToStart = concurrentCalls - sf.active_calls()
    log.info('Finished sending all calls')


def on_stop():
    global data
    global stopped
    global start_timer
    global cur_save_path

    log.info('stop calls')
    start_timer.cancel()
    sf.thread_register('on_stop')
    sf.hangup_all()
    stopped = True
    stop_event.set()
    data['pending'] = sf.pending_calls()
    data['answered'] = sf.answered_calls()
    data['abandoned'] = sf.abandoned_calls()
    data['rejected'] = sf.rejected_calls()
    data['total'] = sf.total_calls()
    socketio.emit('stats', {'pending': data['pending'], 'answered': data['answered'], 'abandoned': data['abandoned'],
                            'rejected': data['rejected'], 'total': data['total']})
    # save CDR
    csv_filename = 'call_details_record.csv'
    csv_filename_path = os.path.join(cur_save_path, csv_filename)
    cur_folder = os.path.basename(os.path.normpath(cur_save_path))
    if sf.save_cdr(csv_filename_path):
        data['cdr_filename'] = str(os.path.join('download', cur_folder, csv_filename))
    else:
        data['cdr_filename'] = ''
    session['data'] = data
    # merge all transcript files into a single file
    time.sleep(1)  # allow to save all wave files
    transcript_filename = os.path.join(cur_save_path, 'transcripts.txt')
    with open(transcript_filename, 'w', os.O_NONBLOCK) as transcript_file:
        # test if transcript is available
        from google.cloud import speech
        maxTrials = 40  # 2 min
        try:
            speech.SpeechClient()
        except:
            maxTrials = 0  # no wait is needed

        wave_path = os.path.join(cur_save_path, '*.wav')
        for wave_filename in glob.glob(wave_path):
            log.debug('Found wave file %s', wave_filename)
            temp_transcript_filename = wave_filename+'.txt'
            if os.path.isfile(temp_transcript_filename):
                with open(temp_transcript_filename, 'r', os.O_NONBLOCK) as temp_transcript_file:
                    for line in temp_transcript_file:
                        transcript_file.write(line)
                os.remove(temp_transcript_filename)
            else:
                log.debug('Wait for transcript to finish')
                numTrials = 0
                while not os.path.isfile(temp_transcript_filename):
                    if numTrials < maxTrials:
                        time.sleep(3)
                        numTrials += 1
                    else:
                        log.warning('Number of trials exceeded for %s', wave_filename)
                        break
                if numTrials < maxTrials:
                    log.debug('Transcript file is ready to be processed')
                    with open(temp_transcript_filename, 'r', os.O_NONBLOCK) as temp_transcript_file:
                        for line in temp_transcript_file:
                            transcript_file.write(line)
                    os.remove(temp_transcript_filename)
            # convert wave file to amr
            convert_wav2amr(wave_filename)

    data['transcripts_filename'] = str(os.path.join('download', cur_folder, 'transcripts.txt'))
    log.debug('Finished to merge all transcript files')


def ui_list(lst, sep, max_displayed_num=10):
    lst_len = len(lst)
    if 0 == lst_len:
        out = ''
    else:
        out = lst[0]
        for i in range(1, min(max_displayed_num, lst_len)):
            out += sep + lst[i]
    return out


def ui_load_cid_dest_num():
    global data

    if data['callerIdsFile']:
        filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], data['callerIdsFile'])
        lst = jp.JsonParser.loadList(filepath)
        data['callerIds'] = ui_list(lst, ', ')
    else:
        log.warning('No caller IDs file')
        data['callerIds'] = ''
    if data['destPhoneNumFile']:
        filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], data['destPhoneNumFile'])
        lst = jp.JsonParser.loadList(filepath)
        data['destPhoneNum'] = ui_list(lst, '\n')
    else:
        log.warning('No dest. phone num. file')
        data['destPhoneNum'] = ''


@app.route('/login', methods=['GET', 'POST'])
def login():
    global data
    global already_logged
    global currentProviderId

    if 'data' in session:
        del session['data']

    errors = ''
    if request.method == 'POST':
        log.debug('Logging in')
        data['username'] = request.form['username']
        data['password'] = request.form['password']
        if data['username'] == app.config['USERNAME'] and data['password'] == app.config['PASSWORD']:
            already_logged = True
            return redirect(url_for('input_data'))
        errors = 'Invalid Credentials. Please try again.'
    elif request.method == 'GET':
        log.debug('Logging out')
        data = {}
        already_logged = False
    currentProviderId = ''
    return render_template('login.html', data=data, errors=errors)


@app.route('/', methods=['GET', 'POST'])
def input_data():
    global data
    global destno_list
    global cid_list
    global already_logged
    global parser
    global currentProviderId
    global start_timer
    global stopped
    global update_stats_thread

    errors = ''
    if 'data' in session:
        data = session['data']

    if not already_logged:
        return redirect(url_for('login'))

    if 'GET' == request.method:
        if request.values.get('provider'):
            currentProviderId = request.values.get('provider')
            log.info('Current provider ID %s', currentProviderId)
            acc = parser.getSipAccount(currentProviderId)
            if acc is not None:
                data['currentProviderId'] = currentProviderId
                data['callDuration'] = acc['callDurationSec']
                data['callerIdsFile'] = acc['callerIdsFile']
                data['destPhoneNumFile'] = acc['destNumsFile']
                data['phoneNumberPrefix'] = acc['phoneNumberPrefix']
                data['cdr_filename'] = ''
                ui_load_cid_dest_num()
            else:
                data['callerIdsFile'] = ''
                data['callerIds'] = ''
                data['destPhoneNumFile'] = ''
                data['destPhoneNum'] = ''
                log.critical('Invalid account for ID %s', currentProviderId)
            data['accountIds'] = parser.getSipAccountIds()
            # make sure that the account is registered
            currentProviderId = ''
            # clear stats
            sf.clear_stats()
            filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], data['destPhoneNumFile'])
            dest_nums = jp.JsonParser.loadList(filepath)
            sf.set_pending_calls(len(dest_nums))
            data['pending'] = sf.pending_calls()
            data['answered'] = sf.answered_calls()
            data['abandoned'] = sf.abandoned_calls()
            data['rejected'] = sf.rejected_calls()
            data['total'] = sf.total_calls()
            socketio.emit('stats',
                          {'pending': data['pending'], 'answered': data['answered'], 'abandoned': data['abandoned'],
                           'rejected': data['rejected'], 'total': data['total']})
        elif not currentProviderId:
            log.warning('Provider cannot be found in request and current provider ID is empty')
            data['cdr_filename'] = ''
            # use the first account as the default one
            accIds = parser.getSipAccountIds()
            data['accountIds'] = accIds
            if accIds:
                currentProviderId = accIds[0]
                log.info('Current provider ID %s', currentProviderId)
                acc = parser.getSipAccount(currentProviderId)
                if acc is not None:
                    data['currentProviderId'] = currentProviderId
                    data['callDuration'] = acc['callDurationSec']
                    data['callerIdsFile'] = acc['callerIdsFile']
                    data['destPhoneNumFile'] = acc['destNumsFile']
                    data['phoneNumberPrefix'] = acc['phoneNumberPrefix']
                    ui_load_cid_dest_num()
                    currentProviderId = ''
                    sf.clear_stats()
                    filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], data['destPhoneNumFile'])
                    dest_nums = jp.JsonParser.loadList(filepath)
                    sf.set_pending_calls(len(dest_nums))
                    data['pending'] = sf.pending_calls()
                    data['answered'] = sf.answered_calls()
                    data['abandoned'] = sf.abandoned_calls()
                    data['rejected'] = sf.rejected_calls()
                    data['total'] = sf.total_calls()
                else:
                    log.critical('Invalid account for ID %s', currentProviderId)
            else:
                data['callerIdsFile'] = ''
                data['callerIds'] = ''
                data['destPhoneNumFile'] = ''
                data['destPhoneNum'] = ''
                log.warning('No provider found')
        else:
            log.info('Updating only stats')
            data['pending'] = sf.pending_calls()
            data['answered'] = sf.answered_calls()
            data['abandoned'] = sf.abandoned_calls()
            data['rejected'] = sf.rejected_calls()
            data['total'] = sf.total_calls()

        data['readonly'] = 'false'
        return render_template('main.html', errors=errors, data=data)
    elif 'POST' == request.method:
        # stop button pressed
        if 'Stop' == request.form['button']:
            on_stop()
            data['readonly'] = 'false'
            session['data'] = data
            errors = sf.error()
            return render_template('main.html', errors=errors, data=data, started=False)

        # clear button pressed
        if 'Clear' == request.form['button']:
            data['callerIdsFile'] = ''
            data['callerIds'] = ''
            cid_list = []
            data['destPhoneNumFile'] = ''
            data['destPhoneNum'] = ''
            destno_list = []
            session['data'] = data
            if not parser.clearCidDestNums(data['currentProviderId']):
                log.critical('Cannot clear dest nums for provider %s', data['currentProviderId'])
            return render_template('main.html', errors=errors, data=data, started=False)

        # new provider button pressed
        if 'New' == request.form['button']:
            return render_template('new_provider.html')

        # remove provider button pressed
        if 'Remove' == request.form['button']:
            accId = request.form['provider']
            rc = sf.remove_account(accId)
            if not rc:
                errors = sf.error()
                return render_template('main.html', errors=errors, data=data)
            rc = parser.removeSipAccount(accId)
            if not rc:
                errors = 'Cannot remove account'
                return render_template('main.html', errors=errors, data=data)
            data['accountIds'] = parser.getSipAccountIds()
            # select the first account in the list as the default one
            if 0 < len(data['accountIds']):
                currentProviderId = data['accountIds'][0]
                log.info('Current provider ID %s', currentProviderId)
                acc = parser.getSipAccount(currentProviderId)
                if acc is not None:
                    data['currentProviderId'] = currentProviderId
                    data['callDuration'] = acc['callDurationSec']
                    data['callerIdsFile'] = acc['callerIdsFile']
                    data['destPhoneNumFile'] = acc['destNumsFile']
                    data['phoneNumberPrefix'] = acc['phoneNumberPrefix']
                    ui_load_cid_dest_num()
                    currentProviderId = ''
                    sf.clear_stats()
                    filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], data['destPhoneNumFile'])
                    dest_nums = jp.JsonParser.loadList(filepath)
                    sf.set_pending_calls(len(dest_nums))
                    data['pending'] = sf.pending_calls()
                    data['answered'] = sf.answered_calls()
                    data['abandoned'] = sf.abandoned_calls()
                    data['rejected'] = sf.rejected_calls()
                    data['total'] = sf.total_calls()
                else:
                    log.warning('Cannot get SIP account for ID %s', currentProviderId)
                    data['callDuration'] = ''
                    data['destPhoneNumFile'] = ''
                    data['phoneNumberPrefix'] = ''
            else:
                log.debug('Empty account list')
                data['callDuration'] = ''
                data['destPhoneNumFile'] = ''
                data['phoneNumberPrefix'] = ''
            return render_template('main.html', data=data, started=False)

        # add button pressed from add new provider page
        if 'Add' == request.form['button']:
            # validate form
            if not request.form['sipServer']:
                errors = 'SIP server address cannot be empty'
                return render_template('new_provider.html', errors=errors)
            try:
                if request.form['port']:
                    sipPort = int(request.form['port'])
                    if 0 > sipPort or 65535 < sipPort:
                        raise ValueError('Invalid port number')
                else:
                    raise ValueError('Port number is empty')
            except:
                errors = 'Invalid port number'
                return render_template('new_provider.html', errors=errors)
            if not request.form['userName']:
                errors = 'Username cannot be empty'
                return render_template('new_provider.html', errors=errors)
            if not request.form['password']:
                errors = 'Password cannot be empty'
                return render_template('new_provider.html', errors=errors)
            try:
                if request.form['concurrentCalls']:
                    concurrentCalls = int(request.form['concurrentCalls'])
                    if 1 > concurrentCalls:
                        raise ValueError('Invalid concurrent calls number')
                else:
                    raise ValueError('Concurrent calls is empty')
            except:
                errors = 'Invalid concurrent calls number'
                return render_template('new_provider.html', errors=errors)
            # add account to config file
            rc = parser.addSipAccount(request.form['sipServer'], request.form['port'], request.form['transportType'],
                                      request.form['concurrentCalls'], request.form['userName'], request.form['password'])
            if not rc:
                errors = 'Cannot add account'
                return render_template('main.html', errors=errors, data=data)
            data['accountIds'] = parser.getSipAccountIds()
            return render_template('main.html', data=data, started=False)

        # cancel button pressed from add new provider page
        if 'Cancel' == request.form['button']:
            return render_template('main.html', data=data, started=False)

        # get provider
        if 'provider' not in request.form:
            errors = 'You must add at least one provider'
            session['data'] = data
            return render_template('main.html', errors=errors, data=data)
        if '' != currentProviderId and currentProviderId != request.form['provider'] or '' == currentProviderId:
            registerAccount = True
            currentProviderId = request.form['provider']
            log.debug('Need to register provider %s', currentProviderId)
            acc = parser.getSipAccount(currentProviderId)
            if acc is None:
                errors = 'You must add at least one provider'
                session['data'] = data
                return render_template('main.html', errors=errors, data=data)
            data['currentProviderId'] = currentProviderId
            data['concurrentCalls'] = int(acc['concurrentCalls'])
            data['callerIdsFile'] = acc['callerIdsFile']
            data['destPhoneNumFile'] = acc['destNumsFile']
        else:
            log.debug('Do not register provider %s', currentProviderId)
            registerAccount = False
            # need to get the number of concurrent calls
            acc = parser.getSipAccount(currentProviderId)
            if acc is None:
                errors = 'You must add at least one provider'
                session['data'] = data
                return render_template('main.html', errors=errors, data=data)
            data['currentProviderId'] = currentProviderId
            data['concurrentCalls'] = int(acc['concurrentCalls'])
            data['callerIdsFile'] = acc['callerIdsFile']
            data['destPhoneNumFile'] = acc['destNumsFile']

        # get call duration
        try:
            callDuration = int(request.form['callDuration'])
            # negative call durations are allowed to disable the timer
        except:
            errors = 'Invalid call duration'
            session['data'] = data
            return render_template('main.html', errors=errors, data=data)
        data['callDuration'] = callDuration

        # get caller IDs
        cid_list = []
        if not data['callerIdsFile']:
            log.debug('No CID file found')
            callerIds = request.form['callerIds']
            if callerIds:
                tok = callerIds.split(',')
                for t in tok:
                    cid_list.append(str(t.strip()))
                if 0 < len(cid_list):
                    filename = secure_filename(currentProviderId + '_cid.txt')
                    filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], filename)
                    jp.JsonParser.saveList(cid_list, filepath)
                    data['callerIdsFile'] = filename
        # use either the edit box or the provided file
        if 0 == len(cid_list):
            file = request.files['callerIdsFile']
            if file:
                filename = secure_filename(currentProviderId+'_cid.txt')
                filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], filename)
                try:
                    file.save(filepath)
                    data['callerIdsFile'] = filename
                except:
                    errors = 'Cannot save ' + filepath
                    session['data'] = data
                    return render_template('main.html', errors=errors, data=data)
                try:
                    cid_list = jp.JsonParser.loadList(filepath)
                    if 0 == len(cid_list):
                        raise ValueError('Empty CID list')
                except:
                    errors = 'Cannot open ' + filepath
                    session['data'] = data
                    return render_template('main.html', errors=errors, data=data)
            elif data['callerIdsFile']:
                filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], data['callerIdsFile'])
                cid_list = jp.JsonParser.loadList(filepath)
            else:
                log.warning('No CLI file is used')
        log.info('Using %d caller IDs', len(cid_list))
        data['callerIds'] = ui_list(cid_list, ', ')

        # get destination phone numbers
        destno_list = []
        if not data['destPhoneNumFile']:
            log.debug('No dest. phone num. file')
            destPhoneNum = request.form['destPhoneNum']
            if destPhoneNum:
                tok = destPhoneNum.split()
                for t in tok:
                    destno_list.append(t.strip())
                if 0 < len(destno_list):
                    filename = secure_filename(currentProviderId + '_destno.txt')
                    filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], filename)
                    jp.JsonParser.saveList(destno_list, filepath)
                    data['destPhoneNumFile'] = filename
        if 0 == len(destno_list):
            file = request.files['destPhoneNumFile']
            if file:
                filename = secure_filename(currentProviderId+'_destno.txt')
                filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], filename)
                try:
                    file.save(filepath)
                    data['destPhoneNumFile'] = filename
                except:
                    errors = 'Cannot save ' + filepath
                    session['data'] = data
                    return render_template('main.html', errors=errors, data=data)
                try:
                    destno_list = jp.JsonParser.loadList(filepath)
                    if 0 == len(destno_list):
                        raise ValueError('Empty destination phone numbers list')
                except:
                    errors = 'Cannot open' + filepath
                    session['data'] = data
                    return render_template('main.html', errors=errors, data=data)
            elif data['destPhoneNumFile']:
                filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], data['destPhoneNumFile'])
                destno_list = jp.JsonParser.loadList(filepath)
            else:
                log.warning('No dest. num. file is used')
        if 0 == len(destno_list):
            errors = 'No destination phone numbers'
            session['data'] = data
            return render_template('main.html', errors=errors, data=data)
        log.info('Using %d dest. nums.', len(destno_list))
        data['destPhoneNum'] = ui_list(destno_list, '\n')

        # get phone number prefix
        data['phoneNumberPrefix'] = request.form['phoneNumberPrefix']

        # save account params
        if not parser.addSipAccountParams(currentProviderId, data['callDuration'], data['callerIdsFile'],
                                          data['destPhoneNumFile'], data['phoneNumberPrefix']):
            log.critical('Cannot save account parameters for account ID %s', currentProviderId)

        if registerAccount:
            log.info('Registering account ' + currentProviderId)
            sf.thread_register('input_data')
            rc = sf.register_account(acc['address'], acc['port'], acc['transport'],
                                     acc['username'], acc['password'])
            if not rc:
                errors = 'Cannot register account'
                err = sf.error()
                if err:
                    errors += ' ('+err+')'
                currentProviderId = ''  # force provider registration
                data['accountIds'] = parser.getSipAccountIds()
                session['data'] = data
                return render_template('main.html', errors=errors, data=data)
            sf.set_null_sound_devices()
        else:
            log.info('Account already registered')

        sf.clear_stats()
        data['pending'] = sf.pending_calls()
        data['answered'] = sf.answered_calls()
        data['abandoned'] = sf.abandoned_calls()
        data['rejected'] = sf.rejected_calls()
        data['total'] = sf.total_calls()
        socketio.emit('stats',
                      {'pending': data['pending'], 'answered': data['answered'], 'abandoned': data['abandoned'],
                       'rejected': data['rejected'], 'total': data['total']})

        stopped = False

        # start thread for generating calls
        stop_event.clear()
        start_timer = Timer(0, on_start,
                            [data['concurrentCalls'], data['callDuration'], data['phoneNumberPrefix']])
        log.info("********************* Start generating calls *********************")
        start_timer.start()

        data['readonly'] = 'true'
        data['cdr_filename'] = ''
        session['data'] = data
        sf.error()  # reset previous errors
        return render_template('main.html', data=data, started=True)
    else:
        log.debug('Method %s', request.method)


@socketio.on('connect')
def on_connect():
    global update_stats_thread
    if not update_stats_thread:
        update_stats_thread = socketio.start_background_task(target=update_stats)


def log_cb(level, msg, len):
    log.log(level, msg)


if __name__ == '__main__':
    if log_to_file:
        log_file_name = 'websoftphone.log'
        if os.path.exists(log_file_name):
            os.remove(log_file_name)
        log.basicConfig(filename=log_file_name, level=log.DEBUG,
                        format='%(asctime)s [%(process)d] [%(thread)d] [%(levelname)s] %(message)s (%(filename)s:%(lineno)s)')
    else:
        my_logger = log.getLogger()
        my_logger.setLevel(log.DEBUG)
        handler = hnds.SysLogHandler(address='/dev/log')
        formatter = log.Formatter('WebAutoDialer[%(process)d] [%(thread)d] [%(levelname)s] %(message)s (%(filename)s:%(lineno)s)')
        handler.setFormatter(formatter)
        my_logger.addHandler(handler)

    # set environment variable for Google Cloud Speech API application
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = app.config['GOOGLE_API_CREDENTIALS']

    rnd.seed()
    default_port = 8000
    if 1 < len(sys.argv):
        try:
            default_port += int(sys.argv[1])
            if 0 > default_port or 65535 < default_port:
                raise ValueError('Invalid port number')
        except:
            log.critical('Command line parameter must be an integer')
            sys.exit(-1)
    log.info('Starting WebAutodialer on port %d', default_port)
    socketio.run(app, host='0.0.0.0', port=default_port, debug=True, use_reloader=False)
