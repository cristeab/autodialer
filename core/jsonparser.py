#!/usr/bin/env python

import json
import os
import logging as log


class JsonParser:
    __filename = None

    def __init__(self, filename):
        self.__filename = filename

    @staticmethod
    def generateId(address, port, transport, username):
        return username + '@' + address + ':' + port + ' - ' + transport

    def getSipAccountIds(self):
        if not os.path.isfile(self.__filename):
            log.critical('File does not exist %s', self.__filename)
            return []
        with open(self.__filename, 'r', os.O_NONBLOCK) as json_file:
            try:
                data = json.load(json_file)
            except ValueError as e:
                log.critical('Cannot open %s: %s', self.__filename, e.message)
                return []
            if data and 'sip_accounts' in data:
                ids = []
                for acc in data['sip_accounts']:
                    accId = JsonParser.generateId(acc['address'], acc['port'], acc['transport'], acc['username'])
                    ids.append(accId)
                return ids
        log.critical('No accounts found %s', self.__filename)
        return []

    def __getSipAccountIndex(self, data, address, port, transport, username, password):
        index = 0
        for acc in data['sip_accounts']:
            if acc['address'] == address and acc['port'] == str(port) and acc['transport'] == transport and acc[
                'username'] == username and acc['password'] == password:
                return index
            index += 1
        return -1

    def clearCidDestNums(self, accId):
        log.debug('Clear CID & dest. nums. for %s', accId)
        if not os.path.isfile(self.__filename):
            log.critical('File does not exist %s', self.__filename)
            return False
        with open(self.__filename, 'r', os.O_NONBLOCK) as json_file:
            try:
                data = json.load(json_file)
            except ValueError as e:
                log.critical('Cannot open %s: %s', self.__filename, e.message)
                return False
        if data and 'sip_accounts' in data:
            acc_list = data['sip_accounts']
            for index, acc in enumerate(acc_list):
                curAccId = JsonParser.generateId(acc['address'], acc['port'], acc['transport'], acc['username'])
                if curAccId == accId:
                    acc['callerIdsFile'] = ''
                    acc['destNumsFile'] = ''
                    acc_list[index] = acc
                    data['sip_accounts'] = acc_list
                    with open(self.__filename, 'w+', os.O_NONBLOCK) as json_file:
                        json.dump(obj=data, fp=json_file, indent=3, separators=(',', ':'), sort_keys=True)
                        return True
        return False

    def addSipAccountParams(self, accId, callDurationSec, callerIdsFile, destNumsFile, phoneNumberPrefix):
        log.debug('Add SIP account params for %s', accId)
        if not os.path.isfile(self.__filename):
            log.critical('File does not exist %s', self.__filename)
            return False
        with open(self.__filename, 'r', os.O_NONBLOCK) as json_file:
            try:
                data = json.load(json_file)
            except ValueError as e:
                log.critical('Cannot open %s: %s', self.__filename, e.message)
                return False
        if data and 'sip_accounts' in data:
            acc_list = data['sip_accounts']
            for index, acc in enumerate(acc_list):
                curAccId = JsonParser.generateId(acc['address'], acc['port'], acc['transport'], acc['username'])
                if curAccId == accId:
                    acc['callDurationSec'] = callDurationSec
                    acc['callerIdsFile'] = callerIdsFile
                    acc['destNumsFile'] = destNumsFile
                    acc['phoneNumberPrefix'] = str(phoneNumberPrefix)
                    acc_list[index] = acc
                    data['sip_accounts'] = acc_list
                    with open(self.__filename, 'w+', os.O_NONBLOCK) as json_file:
                        json.dump(obj=data, fp=json_file, indent=3, separators=(',', ':'), sort_keys=True)
                        return True
        return False

    def addSipAccount(self, address, port, transport, concurrentCalls, username, password):
        log.debug('Add SIP account for user %s', username)
        data = {}
        if os.path.isfile(self.__filename):
            with open(self.__filename, 'r', os.O_NONBLOCK) as json_file:
                try:
                    data = json.load(json_file)
                except ValueError as e:
                    log.critical('Cannot open %s: %s', self.__filename, e.message)
                    return False
            if data and 'sip_accounts' in data:
                if 0 <= self.__getSipAccountIndex(data, address, port, transport, username, password):
                    return True  # nothing to do
            else:
                return False
        else:
            # file does not exist -> create it
            data['sip_accounts'] = []
        data['sip_accounts'].append(
            {'address': address, 'port': str(port), 'transport': transport,
             'concurrentCalls': concurrentCalls, 'username': username, 'password': password,
             'callDurationSec': '', 'callerIdsFile': '', 'destNumsFile': '', 'phoneNumberPrefix': ''})
        with open(self.__filename, 'w+', os.O_NONBLOCK) as json_file:
            json.dump(obj=data, fp=json_file, indent=3, separators=(',', ':'), sort_keys=True)
            return True
        return False

    def removeSipAccount(self, accId):
        log.debug('Remove SIP account for %s', accId)
        if not os.path.isfile(self.__filename):
            log.critical('File does not exist %s', self.__filename)
            return False
        with open(self.__filename, 'r', os.O_NONBLOCK) as json_file:
            try:
                data = json.load(json_file)
            except ValueError as e:
                log.critical('Cannot open %s: %s', self.__filename, e.message)
                return False
        if data and 'sip_accounts' in data:
            idx = 0
            for acc in data['sip_accounts']:
                curAccId = JsonParser.generateId(acc['address'], acc['port'], acc['transport'], acc['username'])
                if curAccId == accId:
                    data['sip_accounts'].pop(idx)
                    with open(self.__filename, 'w', os.O_NONBLOCK) as json_file:
                        json.dump(obj=data, fp=json_file, indent=3, separators=(',', ':'), sort_keys=True)
                    return True
                idx += 1
        return False

    def getSipAccount(self, accId):
        log.debug('Get SIP account for %s', accId)
        if not os.path.isfile(self.__filename):
            log.critical('File does not exist %s', self.__filename)
            return None
        with open(self.__filename, 'r', os.O_NONBLOCK) as json_file:
            try:
                data = json.load(json_file)
            except ValueError as e:
                log.critical('Cannot open %s: %s', self.__filename, e.message)
                return None
        if data and 'sip_accounts' in data:
            for acc in data['sip_accounts']:
                curAccId = JsonParser.generateId(acc['address'], acc['port'], acc['transport'], acc['username'])
                if curAccId == accId:
                    return {'address': str(acc['address']), 'port': str(acc['port']),
                            'transport': str(acc['transport']),
                            'concurrentCalls': str(acc['concurrentCalls']),
                            'username': str(acc['username']), 'password': str(acc['password']),
                            'callDurationSec': str(acc['callDurationSec']),
                            'callerIdsFile': str(acc['callerIdsFile']),
                            'destNumsFile': str(acc['destNumsFile']),
                            'phoneNumberPrefix': str(acc['phoneNumberPrefix'])}
        return None

    @staticmethod
    def saveList(lst, filename):
        if 0 == len(lst):
            return False
        with open(filename, 'w', os.O_NONBLOCK) as list_file:
            for item in lst:
                list_file.write(str(item) + '\n')
        return True

    @staticmethod
    def loadList(filename):
        lst = []
        if not os.path.isfile(filename):
            log.critical('File does not exist %s', filename)
            return lst
        with open(filename, 'r', os.O_NONBLOCK) as list_file:
            for line in list_file:
                item = line.strip()
                if item:
                    item = item.translate(None, '()- ')
                    lst.append(item)
        return lst


# used only for testing purposes
# if __name__ == '__main__':
#     parser = JsonParser('../providers.json')
#     print parser.getSipAccountIds()
#     print parser.addSipAccount('sip.server3.com', '5060', 'UDP', 'CONFIG', '1', 'user3', 'secret3')
#     accId = 'user3@sip.server3.com:5060 - UDP'
#     print parser.addSipAccountParams(accId, 'AutoDialer.wav', 10, 20, 30, 'cid.txt', 'dest_num.txt', '+4')
#     accIds = parser.getSipAccountIds()
#     for ai in accIds:
#         print ai
#         print parser.getSipAccount(ai)
#     print parser.removeSipAccount(accId)
#     print parser.getSipAccountIds()
