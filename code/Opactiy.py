import base64
import bitcoinlib
import json
import math
import mimetypes
from joblib import Parallel, delayed, parallel_backend
import requests
import shutil
import web3
import os
from Crypto.Hash import keccak
from Helper import Helper
from FileMetaData import FileMetaData
from FolderMetaData import FolderMetaData, FolderMetaFolder, FolderMetaFile, FolderMetaFileVersion
from AesGcm256 import AesGcm256
from Constants import Constants
from AccountStatus import AccountStatus
import posixpath
import queue
import time
from threading import Thread
from multiprocessing import Process


class Opacity:
    _baseUrl = "https://broker-1.opacitynodes.com:3000/api/v1/"
    _privateKey = ""
    _chainCode = ""
    _masterKey = None
    _status = None
    _metaData = FolderMetaData()
    _queue = queue.Queue()

    def __init__(self, account_handle):

        if len(account_handle) != 128:
            raise AttributeError("The Account handle should have the length of 128")

        self._privateKey = account_handle[0:64]
        self._chainCode = account_handle[64:128]

        private_key_bytes = bytearray.fromhex(self._privateKey)
        chain_code_bytes = bytearray.fromhex(self._chainCode)

        new_key = bitcoinlib.keys.Key(import_key=private_key_bytes, is_private=True, compressed=True)
        self._masterKey = bitcoinlib.keys.HDKey(key=new_key.private_byte, chain=chain_code_bytes)

        self._status = self.checkAccountStatus()

        t = Thread(target=self.handle_queue)
        t.daemon = True
        t.start()

    def handle_queue(self):
        while True:
            if self._queue.empty():
                #print("no queue item")
                time.sleep(2)
            else:
                item = self._queue.get()
                #print("got queue item")
                print(item)
                if item["action"] == "upload":
                    self.upload(item["information"]["file_path"], item["information"]["opacity_path"])
                elif item["action"] == "delete":
                    self.delete(item["information"]["opacity_path"], item["information"]["handle"])
                elif item["action"] == "move":
                    self.move(item["information"]["from_folder"],
                              item["information"]["object"],
                              item["information"]["to_folder"])
                else:
                    print("not implemented yet")

    def checkAccountStatus(self):
        '''
            fetches the Account data from opacity and returns an status object
        '''
        requestBody = dict()
        requestBody["timestamp"] = Helper.GetUnixMilliseconds()
        rawPayload = Helper.GetJson(requestBody)

        payload = self.signPayloadDict(rawPayload)
        payloadJson = Helper.GetJson(payload)

        with requests.Session() as s:
            response = s.post(self._baseUrl + "account-data", data=payloadJson)

        if response.status_code == 404:
            raise AttributeError("The provided account handle is invalid!")
        else:
            accountData = response.content.decode("utf-8")
            return AccountStatus.ToObject(accountData)

    def signPayloadDict(self, requestBodyJson):
        # hash the payload
        msgBytes = bytearray(requestBodyJson, "utf-8")
        msgHashHex = keccak.new(data=msgBytes, digest_bits=256).hexdigest()
        msgHash = bytearray.fromhex(msgHashHex)

        # create the signature
        privKey = web3.Account.from_key(self._privateKey)
        signature = privKey.signHash(msgHash)

        # signatureFinal = format(signature.r, 'x') + format(signature.s, 'x')
        signatureFinal = signature.signature.hex()[2:130]
        if len(signatureFinal) != 128:
            raise Exception("signature doesn't have the length of 128")

        # print(signatureFinal)

        # get public key as hex
        pubHex = self._masterKey.public_compressed_hex

        newDict = dict()
        newDict["requestBody"] = requestBodyJson
        newDict["signature"] = signatureFinal
        newDict["publicKey"] = pubHex
        newDict["hash"] = msgHashHex

        return newDict

    def upload(self, pathToFile, uploadToFolder):
        if uploadToFolder[0] != "/":
            raise EnvironmentError("Please make sure that your upload destination starts with a '/'."
                                   "\nThe main folder equals '/'."
                                   "\nAnd a subdirectory is defined as '/subdir/subdirofsubdir'")

        if os.path.isfile(pathToFile):
            self.uploadFile(pathToFile, uploadToFolder)
        elif os.path.isdir(pathToFile):
            self.uploadFolder(pathToFile, uploadToFolder)
        else:
            raise EnvironmentError("The path is neither a file nor a folder. Make sure the path is correct")

    def uploadFolder(self, folderPath, uploadToFolder):

        '''
            1. create metadata for the main folder and attach the metadatahandle to the parent folder
            2. go into the main folder and upload there the folders/files

        '''
        folderName = os.path.basename(folderPath)

        finalPath = posixpath.join(uploadToFolder, folderName)
        metadata = self.createMetadata(finalPath)
        if metadata["addFolder"]:
            folder = FolderMetaFolder(name=folderName, handle=metadata["metadataKey"])
            self.AddFileToFolderMetaData(uploadToFolder, folder, isFolder=True)
            print("Created successfully {}".format(folderPath))

        for fileOrFolder in os.listdir(folderPath):
            path = os.path.join(folderPath, fileOrFolder)
            self.upload(path, finalPath)

    def uploadFile(self, filePath, folder) -> bool:

        fd = dict()
        fd["fullName"] = os.path.normpath(filePath)
        fd["name"] = os.path.basename(filePath)
        if os.path.getsize(filePath) == 0:
            print(f"Couldn't upload: {fd['fullName']}\nBecause the filesize is equal to 0.")
            return False
        else:
            fd["size"] = os.path.getsize(filePath)
        fd["type"] = mimetypes.guess_type(filePath)[0]
        # fd["type"] = "application/octet-stream"

        '''
            Check first if the file exists already in the metadata
            -> If yes skip all of this
        '''
        metadataToCheckIn = self.getFolderData(folder=folder)
        for file in metadataToCheckIn["metadata"].files:
            if file.name == fd["name"]:
                print("File: {} already exists".format(fd["name"]))
                return
        else:
            print("Uploading file: {}".format(fd["name"]))

        metaData = FileMetaData(fd)
        uploadSize = Helper.GetUploadSize(fd["size"])
        endIndex = Helper.GetEndIndex(uploadSize, metaData.p)

        handle = Helper.GenerateFileKeys()
        hashBytes = handle[0:32]
        keyBytes = handle[32:]

        metaDataJson = Helper.GetJson(metaData.getDict())

        encryptedMetaData = AesGcm256.encryptString(metaDataJson, keyBytes)

        handleHex = handle.hex()
        fileId = hashBytes.hex()

        requestBody = dict()
        requestBody["fileHandle"] = fileId
        requestBody["fileSizeInByte"] = uploadSize
        requestBody["endIndex"] = endIndex

        requestBodyJson = Helper.GetJson(requestBody)
        payload = self.SignPayloadForm(requestBodyJson, {"metadata": encryptedMetaData})
        with requests.Session() as s:
            response = s.post(self._baseUrl + "init-upload", files=payload)

        if response.status_code != 200:
            raise Exception("Error during init-upload\n{}".format(response.content.decode()))
        '''
            Uploading Parts
        '''

        # start_time = time.time()
        Parallel(n_jobs=8)(delayed(self.uploadPart)(fd, metaData, handle, index, endIndex) for index in range(endIndex))
        # print("--- %s seconds ---" % (time.time() - start_time))

        # for index in range(endIndex):
        #    #start_time = time.time()
        #    print("Uploading file %s part %d/%d" % (fd["name"], index, endIndex))
        #    self.uploadPart(fd, metaData, handle, index, endIndex)
        #    #print("--- %s seconds ---" % (time.time() - start_time))

        '''
            Verify Upload & Retry missing parts
        '''
        requestBody = dict()
        requestBody["fileHandle"] = fileId
        requestBodyJson = Helper.GetJson(requestBody)
        payload = self.signPayloadDict(requestBodyJson)
        payloadJson = Helper.GetJson(payload)

        with requests.Session() as s:
            response = s.post(self._baseUrl + "upload-status", data=payloadJson)

        retries = 3
        content = json.loads(response.content.decode())
        if content["status"] != 'File is uploaded':
            if content["status"] == 'chunks missing':
                missing_parts = content["missingIndexes"]
                while len(missing_parts) > 0 and retries > 0:
                    amount = content["endIndex"]
                    for missingPart in missing_parts:
                        print("Trying to re-upload part {} out of {}".format(missingPart, amount))
                        self.uploadPart(fd, metaData, handle, missingPart-1, endIndex)
                    with requests.Session() as s:
                        response = s.post(self._baseUrl + "upload-status", data=payloadJson)
                        retries -= 1
                    content = json.loads(response.content.decode())
                    if content["status"] == "File is uploaded":
                        break
                    else:
                        if retries == 0:
                            print(f"Failed to upload the {fd['name']}\nReason: Too many retries")
                            return
                        missing_parts = content["missingIndexes"]
            else:
                raise AssertionError("Unknown status of upload-status")

        '''
            Add file to the metadata
        '''

        fileInfo = FolderMetaFile()
        fileInfo.name = fd["name"]
        fileInfo.created = int(os.path.getctime(fd["fullName"]) * 1000)
        fileInfo.modified = int(os.path.getmtime(fd["fullName"]) * 1000)
        # fileInfo.created = Helper.GetUnixMilliseconds()
        # fileInfo.modified = Helper.GetUnixMilliseconds()
        # fileInfo.type = "file"
        fileInfo.versions.append(
            FolderMetaFileVersion(
                size=fd["size"],
                handle=handleHex,
                modified=fileInfo.modified,
                created=fileInfo.created,
                # modified=Helper.GetUnixMilliseconds(),
                # created=Helper.GetUnixMilliseconds()
                # modified=int(os.path.getmtime(filePath)),
                # created=int(os.path.getctime(filePath))
            )
        )
        try:
            self.AddFileToFolderMetaData(folder, fileInfo, isFile=True)
            print("Uploaded file: {}".format(fd["name"]))
        except Exception as e:
            print("Failed to attach the file to the folder\nFilehandle: {}\nFolder: {}\nReason: {}".format(handleHex,
                                                                                                           folder, e))

    def SignPayloadForm(self, requestBodyJson, extraPayload):
        # hash the payload
        msgBytes = bytearray(requestBodyJson, "utf-8")
        msgHashHex = keccak.new(data=msgBytes, digest_bits=256).hexdigest()
        msgHash = bytearray.fromhex(msgHashHex)

        # create the signature
        privKey = web3.Account.from_key(self._privateKey)
        signature = privKey.signHash(msgHash)

        # signature2 = format(signature.r, 'x') + format(signature.s, 'x')
        signatureFinal = signature.signature.hex()[2:130]
        # signatureFinal = "685789e0865e9ac9f81c6629d6e069eb104e171d15e68d3085d8e44b3b8954df2f0dd358a8797826ea2584632cc17effaea85317cf2baac3fd8f1f3b884bde6b"
        if (len(signatureFinal) != 128):
            raise Exception("signature doesn't has the length of 128")

        # print(signatureFinal)

        # get public key as hex
        pubHex = self._masterKey.public_compressed_hex

        newDict = dict()
        newDict["requestBody"] = (None, requestBodyJson, "text/plain; charset=utf-8")
        newDict['signature'] = (None, signatureFinal, "text/plain; charset=utf-8")
        newDict['publicKey'] = (None, pubHex, "text/plain; charset=utf-8")
        # newDict["hash"] = msgHashHex

        for payloadKey, payloadValue in extraPayload.items():
            newDict[payloadKey] = payloadValue

        return newDict

    def uploadPart(self, fileInfo, metaData, handle, currentIndex, lastIndex):
        print("Uploading part {} out of {}".format(currentIndex + 1, lastIndex))
        #output.put("Uploading part {} out of {}".format(currentIndex + 1, lastIndex))
        # start_time = time.time()
        try:
            hashBytes = handle[0:32]
            keyBytes = handle[32:]
            fileId = hashBytes.hex()

            partSize = metaData.p.partSize

            rawpart = Helper.GetPartial(fileInfo, partSize, currentIndex)

            numChunks = math.ceil(len(rawpart) / metaData.p.blockSize)
            encryptedBlob = bytearray(0)
            for chunkIndex in range(numChunks):
                remaining = len(rawpart) - (chunkIndex * metaData.p.blockSize)
                if (remaining <= 0):
                    break

                chunkSize = min(remaining, metaData.p.blockSize)
                encryptedChunkSize = chunkSize + Constants.BLOCK_OVERHEAD

                # chunk = bytearray(chunkSize)
                chunk = rawpart[chunkIndex * metaData.p.blockSize: chunkIndex * metaData.p.blockSize + chunkSize]
                encryptedChunk = AesGcm256.encrypt(chunk, keyBytes)

                if (encryptedChunkSize != len(encryptedChunk)):
                    breakpoint()

                encryptedBlob += encryptedChunk

            requestBody = dict()
            requestBody["fileHandle"] = fileId
            requestBody["partIndex"] = currentIndex + 1
            requestBody["endIndex"] = lastIndex

            requestBodyJson = Helper.GetJson(requestBody)

            payload = self.SignPayloadForm(requestBodyJson, {"chunkData": encryptedBlob})

            with requests.Session() as s:
                response = s.post(self._baseUrl + "upload", files=payload)

        except Exception as e:
            print(f"Failed upload of part {currentIndex + 1} out of {lastIndex}\nError: {e.args}")
        # don't handle the response here, since when check upload-status is handling broken uploads
        # print("-- %s seconds ---" % (time.time() - start_time))

    def AddFileToFolderMetaData(self, folder, fileOrFolder, isFile=False, isFolder=False):
        metadata = self.getFolderData(folder=folder)
        keyString = metadata["keyString"]
        folderMetaData = metadata["metadata"]

        if isFile:
            folderMetaData.files.append(fileOrFolder)
        elif isFolder:
            folderMetaData.folders.append(fileOrFolder)
        else:
            raise EnvironmentError("neither file nor folder")

        ## clean out bug deleted files
        # folderMetaData.files = [temp for temp in folderMetaData.files if len(temp.versions)>0]

        folderMetaDataString = folderMetaData.toString()

        encryptedFolderMetaData = AesGcm256.encryptString(folderMetaDataString, bytearray.fromhex(keyString))
        encryptedFolderMetaDataBase64 = base64.b64encode(encryptedFolderMetaData).decode("utf-8")

        AesGcm256.decrypt(encryptedFolderMetaData, bytearray.fromhex(keyString))

        metaReqDict = {
            "timestamp": Helper.GetUnixMilliseconds(),
            "metadataKey": metadata["metadataKey"],
            "metadata": encryptedFolderMetaDataBase64
        }

        metaReqDictJson = Helper.GetJson(metaReqDict)
        payload = self.signPayloadDict(metaReqDictJson)
        payloadJson = Helper.GetJson(payload)

        with requests.Session() as s:
            response = s.post(self._baseUrl + "metadata/set", data=payloadJson)

        return response

    def GetFolderMetaData(self, metaDataKey, keyString):

        timestamp = Helper.GetUnixMilliseconds()
        payload = dict({
            "timestamp": timestamp,
            "metadataKey": metaDataKey
        })
        payloadJson = Helper.GetJson(payload)

        payloadMeta = self.signPayloadDict(payloadJson)

        payloadMetaJson = Helper.GetJson(payloadMeta)

        with requests.Session() as s:
            response = s.post(self._baseUrl + "metadata/get", data=payloadMetaJson)

        resultMetaDataEncrypted = response.content.decode("utf-8")
        resultMetaDataEncryptedJson = json.loads(resultMetaDataEncrypted)
        stringbytes = bytes(resultMetaDataEncryptedJson["metadata"], "utf-8")
        stringDecoded = base64.b64decode(stringbytes)

        decryptedMetaData = AesGcm256.decrypt(stringDecoded, bytearray.fromhex(keyString))
        metaData = decryptedMetaData.decode("utf-8")
        metaData = json.loads(metaData)

        folderMetaData = FolderMetaData.ToObject(metaData)

        return folderMetaData

    def getFolderData(self, folder):
        folderKey = Helper.getFolderHDKey(self._masterKey, folder)
        metaDataKey = Helper.getMetaDataKey(folderKey)
        keyString = keccak.new(data=bytearray(folderKey.private_hex, "utf-8"), digest_bits=256).hexdigest()

        folderMetaData = self.GetFolderMetaData(metaDataKey, keyString)
        self._metaData = folderMetaData
        return {"metadata": folderMetaData, "keyString": keyString, "metadataKey": metaDataKey}

    def showFiles(self):
        maxSize = 15
        if len(self._metaData.folders) > 0:
            print(Fore.BLUE, "\nFolders")
            print(Fore.BLUE, "{:15}  {:20}".format("Foldername", "Filehandle"))
            for folder in self._metaData.folders:
                info = [folder.name, folder.handle]
                if len(folder.name) > maxSize:
                    info[0] = folder.name[:maxSize - 3] + "..."
                print(Fore.WHITE, "{:15}  {}".format(info[0], info[1]))
            print(Style.RESET_ALL,"")

        if len(self._metaData.files) > 0:
            print(Fore.YELLOW, "\nFiles")
            print(Fore.YELLOW, "{:15}  {:11}  {:20}".format("Filename", "Filesize", "Filehandle"))
            for file in self._metaData.files:
                info = [file.name, file.versions[0].size, file.versions[0].handle]
                if len(file.name) > maxSize:
                    info[0] = file.name[:maxSize - 3] + "..."
                if info[1] >= 1000000000:
                    type = "GB"
                elif info[1] >= 1000000:
                    type = "MB"
                else:
                    type = "KB"
                while info[1] >= 1000:
                    info[1] = info[1] / 1000
                print(Fore.WHITE, "{:15}  {:<7.3f} {:3}  {}".format(info[0], info[1], type, info[2]))
            print(Style.RESET_ALL,"")

    def Download_GUI(self, item, folderPath, pathToSave):
        if len(item["handle"]) == 128:
            self.downloadFile(pathToSave, item["handle"])
        elif len(item["handle"]) == 64:
            self.downloadFolder(item, folderPath, pathToSave)

    def downloadFolder(self, item, folderPath, pathToSave):
        folder = item["name"]
        newFolderPath = os.path.join(pathToSave, folder)

        try:
            os.mkdir(newFolderPath)
            print("Created Folder: {}".format(newFolderPath))
        except FileExistsError:
            print("Folder: {} already exists".format(newFolderPath))

        opacitypath = posixpath.join(folderPath, folder)
        metadata = self.getFolderData(opacitypath)

        for folder in metadata["metadata"].folders:
            subitem = {"handle": folder.handle, "name": folder.name}
            self.Download_GUI(subitem, opacitypath, newFolderPath)

        for file in metadata["metadata"].files:
            subitem = {"handle": file.versions[0].handle, "name": file.name}
            self.Download_GUI(subitem, opacitypath, newFolderPath)
        pass

    def Download(self, fileHandle, savingPath):
        if len(fileHandle) == 128:
            self.downloadFile(savingPath, fileHandle)
        elif len(fileHandle) == 64:
            print("Implement folder download")
        else:
            #print(Fore.LIGHTRED_EX, "Please provide a handle with the length of 128 for a file and 64 for a folder")
            print("Please provide a handle with the length of 128 for a file and 64 for a folder")

    def downloadFile(self, savingPath, fileHandle):
        fileId = fileHandle[:64]
        fileKey = fileHandle[64:]
        key = bytearray.fromhex(fileKey)

        payloadJson = json.dumps({"fileID": fileId})
        with requests.Session() as s:
            response = s.post(self._baseUrl + "download", data=payloadJson)

        url = response.content.decode()
        url = json.loads(url)["fileDownloadUrl"]

        # Get file metadata
        with requests.Session() as s:
            response = s.get(url + "/metadata")

        encryptedMetaData = response.content

        # Decrypt file metadata
        decryptedMetaData = AesGcm256.decrypt(encryptedMetaData, key)
        metaData = json.loads(decryptedMetaData)

        uploadSize = Helper.GetUploadSize(metaData["size"])
        partSize = 5245440  # 80 * (Constants.DEFAULT_BLOCK_SIZE + Constants.BLOCK_OVERHEAD)
        parts = int(uploadSize / partSize) + 1

        fileName = metaData["name"].split(".")[0]
        fileName = fileName.rstrip()
        #folderPath = os.path.normpath(savingPath + "/tmp/" + fileName)
        folderPath = os.path.join(savingPath, "tmp", fileName)
        os.makedirs(folderPath, exist_ok=True)

        '''
            Downloading all parts
        '''
        fileUrl = url + "/file"

        print("Downloading file: {}".format(fileName))
        # start_time = time.time()
        Parallel(n_jobs=5)(
            delayed(self.downloadPart)(partNumber, parts, partSize, uploadSize, fileUrl, folderPath) for partNumber in
            range(parts))
        # print("--- %s seconds with parallel n = 5---" % (time.time() - start_time))

        '''
        start_time = time.time()
        for partNumber in range(parts):
            byteFrom = partNumber * partSize
            byteTo = (partNumber + 1) * partSize - 1
            if (byteTo > uploadSize - 1):
                byteTo = uploadSize - 1

            fileBytes = None
            with requests.Session() as s:
                temp = "bytes={}-{}".format(byteFrom, byteTo)
                s.headers.update({"range": temp})
                response = s.get(url=url)

                fileBytes = response.content

            fileToWriteTo = folderPath + "\\" + str(partNumber) + ".part"

            with open(fileToWriteTo, 'wb') as file:
                file.write(fileBytes)
        print("--- %s seconds --- with single " % (time.time() - start_time))
        '''

        '''
            Decrypt the chunks and restore the file
        '''
        print("Joining all parts together")
        chunkSize = metaData["p"]["blockSize"] + Constants.BLOCK_OVERHEAD
        chunksAmount = int(uploadSize / chunkSize) + 1

        #path = os.path.normpath(savingPath + "\\" + metaData["name"])
        path = os.path.join(savingPath, fileName)

        if os.path.exists(path=path):
            os.remove(path=path)

        with open(path, 'ab+') as saveFile:
            fileIndex = 0
            seek = 0
            for chunkIndex in range(chunksAmount):
                chunkRawBytes = None
                with open(os.path.join(folderPath, str(fileIndex) + ".part"), 'rb') as partFile:
                    partFile.seek(seek)
                    toReadBytes = chunkSize
                    if seek + toReadBytes >= os.path.getsize(partFile.name):
                        toReadBytes = os.path.getsize(partFile.name) - seek

                        # if the bytes to read exceed the file in the next iteration of the for loop
                        # you need to go to the next partFile -> seek from start
                        seek = 0
                        fileIndex = fileIndex + 1
                    else:
                        seek = seek + chunkSize

                    chunkRawBytes = partFile.read(toReadBytes)

                decryptedChunk = AesGcm256.decrypt(chunkRawBytes, key)
                saveFile.write(decryptedChunk)

        shutil.rmtree(folderPath)
        tempFolderPath = os.path.dirname(folderPath)
        if len(os.listdir(tempFolderPath)) == 0:
            shutil.rmtree(tempFolderPath)

        print("Finished download of {}".format(fileName))

    def downloadPart(self, partNumber, endPartNumber, partSize, uploadSize, url, folderPath):
        print("Downloading part {:d} out of {:d}".format(partNumber + 1, endPartNumber))
        byteFrom = partNumber * partSize
        byteTo = (partNumber + 1) * partSize - 1
        if (byteTo > uploadSize - 1):
            byteTo = uploadSize - 1

        fileBytes = None
        with requests.Session() as s:
            temp = "bytes={}-{}".format(byteFrom, byteTo)
            s.headers.update({"range": temp})
            response = s.get(url=url)

            fileBytes = response.content

        #fileToWriteTo = folderPath + "\\" + str(partNumber) + ".part"
        fileToWriteTo = os.path.join(folderPath, str(partNumber) + ".part")

        with open(fileToWriteTo, 'wb') as file:
            file.write(fileBytes)


    def rename(self, folder, handle, oldName, newName):

        if len(handle) == 128:
            # only rename the file and set metadata
            metadata = self.getFolderData(folder)
            for file in metadata["metadata"].files:
                if file.versions[0].handle == handle:
                    oldName = file.name
                    file.name = newName + os.path.splitext(os.path.basename(oldName))[1]
                    break
            self.setMetadata(metadata)
            print("Successfully renamed {} into {}".format(oldName, newName))
            pass
        elif len(handle) == 64:
            # create new metadata and for all subfolders also create new metadata
            new_folder_path = posixpath.join(folder, newName)
            old_folder_path = posixpath.join(folder, oldName)
            folderObject = self.createFolder(new_folder_path)
            self.copyMetadata(old_folder_path, new_folder_path)
            self.delete(folder, handle, deleteFiles=False)
            # metadata_to_fill = self.getFolderData(whole_path)
            #
            # metadata = self.getFolderData(folder)
            # folder_to_recreate = [folder for folder in metadata["metadata"].folders if folder.handle == handle][0]
            # to_recreate_path = posixpath.join(folder, folder_to_recreate.name)
            # metadata_recreate = self.getFolderData(to_recreate_path)
            #
            # metadata_to_fill["metadata"].files = metadata_recreate["metadata"].files
            #
            # for folder in metadata_recreate["metadata"].folders:
            #     new_folder_path = posixpath.join(to_recreate_path, folder.name)
            #     print(new_folder_path)
            #
            # self.setMetadata(metadata_to_fill)

        else:
            print("error")


    def copyMetadata(self, folder_from, folder_to):
        metadata_from = self.getFolderData(folder_from)
        if len(metadata_from["metadata"].files) == 0 and len(metadata_from["metadata"].folders) == 0:
            return

        if len(metadata_from["metadata"].files) != 0:
            metadata_to = self.getFolderData(folder_to)
            metadata_to["metadata"].files = metadata_from["metadata"].files
            self.setMetadata(metadata_to)

        for folder in metadata_from["metadata"].folders:
            old_folder_path = posixpath.join(folder_from, folder.name)
            new_folder_path = posixpath.join(folder_to, folder.name)
            _ = self.createFolder(new_folder_path)
            self.copyMetadata(old_folder_path, new_folder_path)

    def setMetadata(self, metadata):
        keyString = metadata["keyString"]

        folderMetaDataString = metadata["metadata"].toString()

        encryptedFolderMetaData = AesGcm256.encryptString(folderMetaDataString, bytearray.fromhex(keyString))
        encryptedFolderMetaDataBase64 = base64.b64encode(encryptedFolderMetaData).decode("utf-8")

        AesGcm256.decrypt(encryptedFolderMetaData, bytearray.fromhex(keyString))

        metaReqDict = {
            "timestamp": Helper.GetUnixMilliseconds(),
            "metadataKey": metadata["metadataKey"],
            "metadata": encryptedFolderMetaDataBase64
        }

        metaReqDictJson = Helper.GetJson(metaReqDict)
        payload = self.signPayloadDict(metaReqDictJson)
        payloadJson = Helper.GetJson(payload)

        with requests.Session() as s:
            response = s.post(self._baseUrl + "metadata/set", data=payloadJson)

        folderMetaData = self.decryptMetaData(response, keyString)
        metadata["metadata"] = folderMetaData

        return metadata

    def decryptMetaData(self, metadataResponse, keyString):
        resultMetaDataEncrypted = metadataResponse.content.decode("utf-8")
        resultMetaDataEncryptedJson = json.loads(resultMetaDataEncrypted)
        stringbytes = bytes(resultMetaDataEncryptedJson["metadata"], "utf-8")
        stringDecoded = base64.b64decode(stringbytes)

        decryptedMetaData = AesGcm256.decrypt(stringDecoded, bytearray.fromhex(keyString))
        metaData = decryptedMetaData.decode("utf-8")
        metaData = json.loads(metaData)

        folderMetaData = FolderMetaData.ToObject(metaData)

        return folderMetaData

    def delete(self, folderPath, handle, skipGetMetadata=False, metadata=None, deleteFiles=True):

        if skipGetMetadata is False:
            metadata = self.getFolderData(folderPath)

        if len(handle) == 128:  # file
            requestBody = dict()
            requestBody["fileID"] = handle[:64]
            rawPayload = Helper.GetJson(requestBody)

            payload = self.signPayloadDict(rawPayload)
            payloadJson = Helper.GetJson(payload)

            with requests.Session() as s:
                response = s.post(self._baseUrl + "delete", data=payloadJson)

            response = response.content.decode()
            # successful delete
            if response == "{}":
                folderMetaData = metadata["metadata"]
                fileToDelete = [file for file in folderMetaData.files if file.versions[0].handle == handle][0]
                folderMetaData.files = [file for file in folderMetaData.files if file.versions[0].handle != handle]
                # metadata changes it files list because folderMetaData is a shallow copy of the metadata
                self.setMetadata(metadata)
                #print(Fore.GREEN, "Successfully deleted the file: {}".format(fileToDelete.name))
                print("Successfully deleted the file: {}".format(fileToDelete.name))
                return folderMetaData
            else:  # file doesn't exist
                #print(Fore.LIGHTRED_EX, "Error:\n{}".format(response))
                print("Error:\n{}".format(response))

        elif len(handle) == 64:  # folder
            # delete subdirectories aswell as subfiles first
            folderMetaData = metadata["metadata"]
            folderToDelete = [folder for folder in folderMetaData.folders if folder.handle == handle][0]
            folderToDeletePath = posixpath.join(folderPath, folderToDelete.name)

            print("Starting to delete {}".format(folderToDeletePath))
            folderToDeleteMetadata = self.getFolderData(folderToDeletePath)
            folders = folderToDeleteMetadata["metadata"].folders
            if len(folders) > 0:
                for folder in folders:
                    self.delete(folderToDeletePath, folder.handle, True, folderToDeleteMetadata, deleteFiles)

            if deleteFiles:
                files = folderToDeleteMetadata["metadata"].files
                if len(files) > 0:
                    for file in files:
                        self.delete("", file.versions[0].handle, True, folderToDeleteMetadata)

            # delte the folder itself
            response = self.deleteMetaData(handle)

            response = json.loads(response.content.decode())
            if response["status"] == "metadata successfully deleted":
                folderMetaData.folders = [folder for folder in folderMetaData.folders if folder.handle != handle]
                response = self.setMetadata(metadata)
                #print(Fore.GREEN, "Finished deleting: {}".format(folderToDeletePath))
                print("Finished deleting: {}".format(folderToDeletePath))
            else:
                #print(Fore.LIGHTRED_EX, "Error:\n{}".format(response))
                print("Error:\n{}".format(response))

        else:
            #print(Fore.LIGHTRED_EX, "Handle hasn't the length of 64 or 128")
            print("Handle hasn't the length of 64 or 128")

    def deleteMetaData(self, handle):
        requestBody = dict()
        requestBody["timestamp"] = Helper.GetUnixMilliseconds()
        requestBody["metadataKey"] = handle
        rawPayload = Helper.GetJson(requestBody)

        payload = self.signPayloadDict(rawPayload)
        payloadJson = Helper.GetJson(payload)

        with requests.Session() as s:
            return s.post(self._baseUrl + "metadata/delete", data=payloadJson)

    def createMetadata(self, folder):
        dictionary = self.createMetadatakeyAndKeystring(folder=folder)
        requestBody = dict()
        requestBody["timestamp"] = Helper.GetUnixMilliseconds()
        requestBody["metadataKey"] = dictionary["metadataKey"]
        rawPayload = Helper.GetJson(requestBody)

        payload = self.signPayloadDict(rawPayload)
        payloadJson = Helper.GetJson(payload)

        with requests.Session() as s:
            response = s.post(self._baseUrl + "metadata/create", data=payloadJson)

        if response.status_code == 403:
            print("The folder: {} already exists! -> Will use that folder instead".format(folder))
            return {"metadataKey": dictionary["metadataKey"], "addFolder": False}
        else:
            # set empty foldermetadata
            newfolderMetadata = FolderMetaData()
            newfolderMetadata.name = os.path.basename(folder)
            newfolderMetadata.created = Helper.GetUnixMilliseconds()
            newfolderMetadata.modified = Helper.GetUnixMilliseconds()
            dictionary["metadata"] = newfolderMetadata

            self.setMetadata(dictionary)
            return {"metadataKey": dictionary["metadataKey"], "addFolder": True}

    def createMetadatakeyAndKeystring(self, folder):
        folder = folder
        folderKey = Helper.getFolderHDKey(self._masterKey, folder)
        metaDataKey = Helper.getMetaDataKey(folderKey)
        keyString = keccak.new(data=bytearray(folderKey.private_hex, "utf-8"), digest_bits=256).hexdigest()

        return {"metadataKey": metaDataKey, "keyString": keyString}


    def move(self, fromFolder, item, toFolder):
        if len(item["handle"]) == 128:  # move file
            print("moving file")

            fromFolderMetadata = self.getFolderData(fromFolder)
            toFolderMetadata = self.getFolderData(toFolder)

            toMoveMetadata = [metadata for metadata in fromFolderMetadata["metadata"].files if
                              metadata.versions[0].handle == item["handle"]]
            if len(toMoveMetadata) == 0:
                raise FileNotFoundError("The specified folder doesn't exist on the path: '{}'".format(fromFolder))
            toMoveMetadata = toMoveMetadata[0]

            fromFolderMetadata["metadata"].files = [metadata for metadata in fromFolderMetadata["metadata"].files if
                                                    metadata.versions[0].handle != toMoveMetadata.versions[0].handle]
            self.setMetadata(fromFolderMetadata)

            toFolderMetadata["metadata"].files.append(toMoveMetadata)
            self.setMetadata(toFolderMetadata)
        elif len(item["handle"]) == 64:  # move folder

            new_folder_path = posixpath.join(toFolder, item["name"])
            old_folder_path = posixpath.join(fromFolder, item["name"])
            _ = self.createFolder(new_folder_path)
            self.copyMetadata(old_folder_path, new_folder_path)
            self.delete(fromFolder, item["handle"], deleteFiles=False)
        else:
            raise Exception("Please provide a file handle with the length of 128 or a folder handle with the length of 64.")

        print(f"Successfully moved {item['name']} from '{fromFolder}' to '{toFolder}'")

    def createFolder(self, folderPath):
        folderName = os.path.basename(folderPath)
        parentDirectory = os.path.dirname(folderPath)
        metadata = self.createMetadata(folderPath)
        if metadata["addFolder"]:
            folder = FolderMetaFolder(name=folderName, handle=metadata["metadataKey"])
            self.AddFileToFolderMetaData(parentDirectory, folder, isFolder=True)
            print("Created successfully {}".format(folderPath))
            return folder
        else:
            return FolderMetaFolder(name=folderName, handle=metadata["metadataKey"])