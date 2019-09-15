#!/usr/bin/env python

import pjsua as pj
import threading as th
import logging as log
from singleton import Singleton
from core import notifier as notif
from core import jsonparser as jp
from transcribe_async import transcribe_file
from werkzeug import secure_filename
import datetime as dt
import os


class _AccountCallback(pj.AccountCallback):
    __event = None
    registrationSuccess = False

    def __init__(self, account=None):
        pj.AccountCallback.__init__(self, account)
        self.__event = notif.Notifier()

    def wait(self):
        return self.__event.twait(10)

    def on_incoming_call(self, call):
        call.hangup(501, "Sorry, not ready to accept calls")

    def on_reg_state(self):
        accInfo = self.account.info()
        log.info('For account with URI %s registration status changed %d (%s)', accInfo.uri, accInfo.reg_status,
                 accInfo.reg_reason)
        if accInfo.reg_status == 200:
            self.registrationSuccess = True
        else:
            self.registrationSuccess = False
            mgr = _ModuleManager.instance()
            mgr.error = accInfo.reg_reason
        self.__event.notify()

    def registered(self):
        if not self.wait():
            mgr = _ModuleManager.instance()
            mgr.error = 'Timeout occured'
            log.critical(mgr.error)
        out = self.registrationSuccess
        self.registrationSuccess = False
        return out


# this callback is used for classical number calling
class _CallCallback(pj.CallCallback):
    state = None
    abandoned = False
    confirmed = False
    dateTimeFormat = '%d/%m/%Y %H:%M:%S'

    def __init__(self, rec_audio_file, call_duration):
        pj.CallCallback.__init__(self, None)
        if 0 <= call_duration:
            self.__callDuration = call_duration
            self.__callDurationTimer = th.Timer(self.__callDuration, self._call_duration_timeout)
        else:
            self.__callDurationTimer = None
            log.warning('Call duration timeout is disabled')
        if rec_audio_file:
            lib = pj.Lib.instance()
            try:
                mgr = _ModuleManager.instance()
                dest_folder = os.path.dirname(rec_audio_file)
                if os.path.isdir(dest_folder):
                    log.debug('Using wave filename %s', rec_audio_file)
                    self.__recorderId = lib.create_recorder(rec_audio_file)
                    self.__recAudioFile = rec_audio_file
                    log.info('Created recorder %d for wave file %s', self.__recorderId, rec_audio_file)
                else:
                    mgr.error = 'Cannot create recorder: destination folder does not exist'
                    self.__recorderId = None
                    log.critical('Folder does not exist %s', dest_folder)
            except pj.Error as e:
                mgr.error = 'Cannot create recorder: ' + e.err_msg()
                self.__recorderId = None
                log.critical('Error when creating recorder for wave file %s: %s', rec_audio_file, e.err_msg())
        # timer to send dtmf after the call is answered
        self.__sendDtmfTimer = th.Timer(2, self._send_dtmf)
        # timer to start recording after DTMF is send
        self.__startRecTimer = th.Timer(1, self._start_recording)
        # CDR related stuff
        self.__startTime = None
        self.__dialedNum = None
        self.__cid = None
        self.__recAudioFile = rec_audio_file

    def __del__(self):
        self.destroy_recorder()

    def destroy_recorder(self):
        if self.__recorderId is not None:
            lib = pj.Lib.instance()
            lib.recorder_destroy(self.__recorderId)
            log.info('Destroyed recorder %d', self.__recorderId)
            self.__recorderId = None
        else:
            log.info('Recorder already destroyed')

    def is_valid(self):
        return self.__recorderId is not None

    def _start_recording(self):
        if self.__recorderId is not None:
            try:
                lib = pj.Lib.instance()
                lib.thread_register('start_recording')
                recorderSlot = lib.recorder_get_slot(self.__recorderId)
                lib.conf_set_tx_level(recorderSlot, 2.0)
                callSlot = self.call.info().conf_slot
                lib.conf_set_rx_level(callSlot, 2.0)
                log.debug('Call slot %d, recorder slot %d', callSlot, recorderSlot)
                lib.conf_connect(callSlot, recorderSlot)
                if self.__callDurationTimer is not None:
                    self.__callDurationTimer.start()
                log.debug('Start recording with timeout %d [s]', self.__callDuration)
            except pj.Error as e:
                log.critical('Cannot start recording: %s', e.err_msg())
            except RuntimeError as e:
                # this copes with the situation when start_recording is called twice
                log.warning('Cannot start recording (this might be a false alarm): %s', e.message)
        else:
            log.critical('Cannot start recording: recorder ID is None')

    def _stop_recording(self):
        if self.__recorderId is not None:
            try:
                lib = pj.Lib.instance()
                recorderSlot = lib.player_get_slot(self.__recorderId)
                lib.conf_disconnect(self.call.info().conf_slot, recorderSlot)
                log.debug('Stop recording')
            except pj.Error as e:
                log.critical('Cannot stop recording: %s', e.err_msg())
            except RuntimeError as e:
                log.critical('Cannot stop recording: %s', e.message)
        else:
            log.critical('Cannot stop recording: player ID is None')

    def _send_dtmf(self):
        try:
            lib = pj.Lib.instance()
            lib.thread_register('send_dtmf')
            self.call.dial_dtmf('*')
            log.info('DTMF sent')
            self.__startRecTimer.start()
        except pj.Error as e:
            log.critical('Cannot send DTMF: %s', e.err_msg())
        except RuntimeError as e:
            log.critical('Cannot send DTMF: %s', e.message)

    def _call_duration_timeout(self):
        log.debug('Call duration timeout')
        try:
            lib = pj.Lib.instance()
            lib.thread_register('call_duration_timeout')
            self.abandoned = True
            self.call.hangup()
            log.debug('Call has been hangup on call duration timeout')
        except pj.Error as e:
            log.critical('Cannot hangup call: %s', e.err_msg())

    def set_cdr(self, dest_no, cli):
        self.__startTime = None
        self.__dialedNum = dest_no
        self.__cid = cli

    def set_cdr_start_time(self):
        self.__startTime = dt.datetime.now()

    def send_cdr(self):
        if self.__startTime is not None:
            mgr = _ModuleManager.instance()
            endTime = dt.datetime.now()
            duration_sec = int((endTime - self.__startTime).total_seconds())
            cdr_item = (
                self.__startTime.strftime(self.dateTimeFormat), endTime.strftime(self.dateTimeFormat), self.__dialedNum,
                duration_sec, self.__cid, self.__recAudioFile)
            mgr.cdr_lock.acquire()
            mgr.cdr.append(cdr_item)
            mgr.cdr_lock.release()
            self.__startTime = None
        else:
            log.critical('Cannot send CDR')

    # Notification when call's media has changed
    def on_state(self):
        call_info = self.call.info()
        self.state = call_info.state
        log.info('Call is %s last_code = %d (%s)', call_info.state_text, call_info.last_code, call_info.last_reason)
        mgr = _ModuleManager.instance()
        if pj.CallState.CONFIRMED == self.state:
            log.debug('Call answered')
            mgr.answered_calls += 1
            self.confirmed = True
            self.set_cdr_start_time()
            self.__sendDtmfTimer.start() # send DTMF with timeout
        elif pj.CallState.DISCONNECTED == self.state:
            log.debug('Call disconnected')
            mgr.active_calls -= 1
            mgr.pending_calls -= 1
            if not self.confirmed:
                if self.abandoned:
                    mgr.abandoned_calls += 1
                else:
                    mgr.rejected_calls += 1
            # no need to disconnect slots
            # stop call timeout if needed
            self.__callDurationTimer.cancel()
            # stop DTMF timer
            self.__sendDtmfTimer.cancel()
            # stop rec timer
            self.__startRecTimer.cancel()
            # destroy recorder
            self.destroy_recorder()
            # send CDR entry
            self.send_cdr()
            # start transcribing the recorded wave file
            th.Timer(0, transcribe_file, [self.__recAudioFile, self.__dialedNum]).start()

    # Notification when call's media state has changed.
    def on_media_state(self):
        if self.call.info().media_state == pj.MediaState.ACTIVE:
            log.debug('Media active')
        else:
            log.info('Media state' + str(self.call.info().media_state))


# PJSUA library initialization/deinitialisation
@Singleton
class _ModuleManager:
    active_calls = 0
    pending_calls = 0
    answered_calls = 0
    abandoned_calls = 0
    rejected_calls = 0
    total_calls = 0
    cdr_lock = th.Lock()
    cdr = []
    error = ''

    def _log_cb(self, level, msg, len):
        log.debug('PJSIP %s', msg)

    def __init__(self):
        self.domain = None
        self.account = None
        self.accountList = {}
        self.lib = None
        self.start_pjsip()

    def __del__(self):
        self.stop_pjsip()

    def start_pjsip(self):
        self.domain = None
        self.account = None
        self.lib = pj.Lib()
        ua_cfg = pj.UAConfig()
        ua_cfg.max_calls = 3000
        ua_cfg.user_agent = 'WebAutoDialer'
        self.lib.init(ua_cfg=ua_cfg, log_cfg=pj.LogConfig(level=5, callback=self._log_cb))
        self.lib.start()
        log.info('Started PJSIP library with maximum calls number ' + str(ua_cfg.max_calls))
        codecs = self.lib.enum_codecs()
        for codec in codecs:
            #adjust codecs priorities
            codec_priority = codec.priority
            if 'G729' in codec.name or 'PCMA' in codec.name:
                codec_priority = 255
                self.lib.set_codec_priority(codec.name, codec_priority)
            log.info('Codec name ' + codec.name + ', priority ' + str(codec_priority))

    def stop_pjsip(self):
        self.domain = None
        self.account = None
        if self.lib is not None:
            self.lib.destroy()
            self.lib = None
            log.info('Stopped PJSIP library')
        else:
            log.info('PJSIP library was not started')

    def start_call(self, nb_to_call, cli, rec_folder, call_duration):
        if not isinstance(nb_to_call, basestring):
            log.critical('Input should be a string')
            return None
        # generate an unique file name
        rec_filename = secure_filename(str(nb_to_call) + '.wav')
        rec_path = os.path.join(rec_folder, rec_filename)
        idx = 0
        while os.path.isfile(rec_path):
            rec_filename = secure_filename(str(nb_to_call) + '_' + str(idx) + '.wav')
            rec_path = os.path.join(rec_folder, rec_filename)
            idx += 1
        # make call
        call_callback = _CallCallback(rec_path, call_duration)
        if not call_callback.is_valid():
            return None
        try:
            call_uri = 'sip:' + str(nb_to_call) + '@' + self.domain
            if cli:
                hdr_list = [
                    ('P-Asserted-Identity', 'sip:' + cli + '@' + self.domain)]  # some PABXes might use this to set the CLI
            else:
                hdr_list = []
            self.account.make_call(dst_uri=call_uri, cb=call_callback, hdr_list=hdr_list)
            call_callback.set_cdr(nb_to_call, cli)
        except pj.Error as e:
            self.error = 'Exception raised in make_call ' + e.err_msg()
            log.critical(self.error)
            return None
        self.active_calls += 1
        self.total_calls += 1
        log.info('Call started')
        return call_callback

    def create_account_config(self, transport_type, domain, username, password):
        # make sure that reg_uri has the correct format for TCP
        if 'TCP' == transport_type:
            reg_uri = 'sip:'
            if username:
                reg_uri += username + '@' + domain
            else:
                reg_uri += domain
            reg_uri += ';transport=tcp'
        else:
            reg_uri = ''
        account_config = pj.AccountConfig(domain=domain, username=username, password=password,
                                        display='', registrar=reg_uri)
        return account_config


# external API
def register_account(domain, port, transportType, username, password):
    """Account registration: should be done first and only once
    Input: domain - address of the SIP server
           port - port of the SIP server
           transportType - transport used by RTP packets: UDP or TCP
           username - user name of the SIP account
           password - password of the SIP account
    Return: True - registration with the SIP server is successful
            False - otherwise
    """

    mgr = _ModuleManager.instance()

    # search in existing accounts
    accId = jp.JsonParser.generateId(domain, port, transportType, username)
    if accId in mgr.accountList:
        mgr.domain = domain
        mgr.account = mgr.accountList[accId]
        mgr.account.set_default()
        log.info('Default account has ID %s', accId)
        return True

    log.info('Creating new account for ID %s', accId)
    try:
        # create transport
        if 'UDP' == transportType:
            transport = mgr.lib.create_transport(pj.TransportType.UDP, pj.TransportConfig(int(port)))
        elif 'TCP' == transportType:
            transport = mgr.lib.create_transport(pj.TransportType.TCP, pj.TransportConfig(int(port)))
        else:
            log.critical('Unknown transport ' + transportType)
            return False
    except pj.Error as e:
        mgr.error = e.err_msg()
        log.critical('Cannot create transport %s', mgr.error)
        return False
    try:
        # create account config
        accountConfig = mgr.create_account_config(transportType, domain, username, password)
        if accountConfig is None:
            return False
    except pj.Error as e:
        mgr.error = e.err_msg()
        log.critical('Cannot create transport %s', mgr.error)
        return False
    try:
        # create account
        account = mgr.lib.create_account(accountConfig)
        account.set_transport(transport)
        account_callback = _AccountCallback(account)
        account.set_callback(account_callback)
        account.set_default()
        if 'TCP' == transportType:
            # force registration
            try:
                account.set_registration(True)
            except pj.Error as e:
                log.warning('Cannot force manual registration: %s', e.err_msg())
    except pj.Error as e:
        mgr.error = e.err_msg()
        log.critical('Cannot create account ID %s', accId, mgr.error)
        return False
    log.debug('Wait to confirm registration')
    if account_callback.registered():
        log.info('Registered account ID %s', accId)
        mgr.domain = domain
        mgr.account = account
        mgr.accountList[accId] = account  # put in the list the new account
        return True
    log.critical('Cannot register account ID %s', accId)
    return False


def remove_account(accId):
    mgr = _ModuleManager.instance()
    if accId not in mgr.accountList:
        log.info('Account ID %s not yet created, can be removed immediately', accId)
        return True
    # delete existing account
    try:
        account = mgr.accountList[accId]
        account.set_registration(False)
        account.delete()
        del mgr.accountList[accId]
    except pj.Error as e:
        mgr.error = e.err_msg()
        log.warning('Cannot delete account ID: %s', mgr.error)
        return True
    log.info('Account ID %s removed', accId)
    return True


def set_null_sound_devices():
    mgr = _ModuleManager.instance()
    mgr.lib.set_null_snd_dev()


def call(number_to_dial, cli, rec_folder, call_duration):
    mgr = _ModuleManager.instance()
    call_cbk = mgr.start_call(number_to_dial, cli, rec_folder, call_duration)
    if call_cbk is None:
        return False
    return True


def thread_register(name):
    mgr = _ModuleManager.instance()
    mgr.lib.thread_register(name)


def hangup_all():
    mgr = _ModuleManager.instance()
    mgr.lib.hangup_all()


def set_pending_calls(pending):
    mgr = _ModuleManager.instance()
    mgr.pending_calls = pending


def pending_calls():
    mgr = _ModuleManager.instance()
    return mgr.pending_calls


def active_calls():
    mgr = _ModuleManager.instance()
    return mgr.active_calls


def answered_calls():
    mgr = _ModuleManager.instance()
    return mgr.answered_calls


def abandoned_calls():
    mgr = _ModuleManager.instance()
    return mgr.abandoned_calls


def rejected_calls():
    mgr = _ModuleManager.instance()
    return mgr.rejected_calls


def total_calls():
    mgr = _ModuleManager.instance()
    return mgr.total_calls


def clear_stats():
    mgr = _ModuleManager.instance()
    mgr.active_calls = 0
    mgr.pending_calls = 0
    mgr.answered_calls = 0
    mgr.abandoned_calls = 0
    mgr.rejected_calls = 0
    mgr.total_calls = 0
    mgr.cdr = []


def error():
    mgr = _ModuleManager.instance()
    out = mgr.error
    mgr.error = ''
    return out


def save_cdr(filename):
    try:
        with open(filename, 'w', os.O_NONBLOCK) as cdr_file:
            mgr = _ModuleManager.instance()
            cdr_file.write('Start Date, End Date, Dialed Number, Duration [s], Caller ID, Audio Recording Full Path\n')
            for item in mgr.cdr:
                cdr_file.write(
                    item[0] + ', ' + item[1] + ', ' + item[2] + ', ' + str(item[3]) + ', ' + item[4] + ', ' + item[
                        5] + '\n')
        return True
    except IOError as e:
        log.critical('Cannot open for writing %s: %s', filename, e.strerror)
    return False
