#!/usr/bin/env python
# -*- coding: utf-8 -*-

from win32com.shell import shell, shellcon
import pythoncom

import os, argparse
from tqdm import tqdm
from typing import List

# 副檔名一律以小寫比對，見 getExtension()。
# iPhone「保留原始檔」模式直出 .heic/.heif/.mov；
# 「自動」模式由手機端即時轉檔為 .jpg/.mov。
IMAGE_EXTS = {
    "jpg", "jpeg", "jpe", "png", "bmp", "gif", "tif", "tiff",
    "heic", "heif", "dng", "webp",
}
VIDEO_EXTS = {"mov", "mp4", "m4v", "avi", "3gp", "mpg", "mpeg"}
SIDECAR_EXTS = {"aae"}  # iPhone 的編輯紀錄，非影像本體

FILTER = IMAGE_EXTS  # 沿用舊名稱，供既有呼叫端使用


def getExtension(fileName):
    """回傳不含點、已轉小寫的副檔名；沒有副檔名時回傳空字串。"""
    return os.path.splitext(fileName)[1].lstrip(".").lower()


def getItemsInside(parentFolderObject, filtering=False):
    nameList = []
    for pidl in parentFolderObject:
        fileName = parentFolderObject.GetDisplayNameOf(pidl, shellcon.SHGDN_NORMAL)
        if not filtering: nameList.append(fileName)
        else:
            extName = getExtension(fileName)
            if extName in FILTER: nameList.append(fileName)
    return nameList

def getFilteringSignals(parentFolderObject):
    filteringSignals = []
    for pidl in parentFolderObject:
        fileName = parentFolderObject.GetDisplayNameOf(pidl, shellcon.SHGDN_NORMAL)
        extName = getExtension(fileName)
        filteringSignals.append(True if extName in FILTER else False)
    return filteringSignals



def ChineseCharacterChecking(folderName):
    if folderName == "Users": return "使用者"
    elif folderName == "Public": return "公用"
    elif folderName == "Desktop": return "桌面"
    elif folderName == "Pictures": return "圖片"
    elif folderName == "Videos": return "影片"
    elif folderName == "Downloads": return "下載"
    else: return folderName

def getFolderObject(folderName, parentFolderObject=None, isWholeName=True, characterChecking=True):
    if characterChecking: folderName = ChineseCharacterChecking(folderName)
    if parentFolderObject is None: parentFolderObject = shell.SHGetDesktopFolder()
    pidl_get = None
    for pidl in parentFolderObject:
        fileName = parentFolderObject.GetDisplayNameOf(pidl, shellcon.SHGDN_NORMAL)
        #print("===========", folderName, fileName)
        if isWholeName:
            if fileName == folderName:
                print("Get object: {}".format(folderName))
                pidl_get = pidl
                break
        else:
            if folderName in fileName:
                print("Get object with partial name \"{}\": {}".format(folderName, fileName))
                pidl_get = pidl
                break
    if pidl_get is None:
        raise FileNotFoundError(
            "找不到「{}」。父資料夾內容為：{}".format(
                folderName, getItemsInside(parentFolderObject)))
    folder = parentFolderObject.BindToObject(pidl_get, None, shell.IID_IShellFolder)
    return folder, pidl_get



def iter_split(s):
    dirFolders = []
    rest, tail = os.path.split(s)
    while tail != "":
        dirFolders.insert(0, tail)
        rest, tail = os.path.split(rest)
    return dirFolders



def checkFolderName(folderName:str):
    print("Checking folder name: {}".format(folderName))
    while "\\" in folderName:
        #print(folderName)
        folderName = folderName[1:]
    print("Finishing checking folder name: {}".format(folderName))
    return folderName



def parseAbsName(winAbsPath:str, rootName="本機"):
    if ":" in winAbsPath:
        pathSplit = winAbsPath.split(":")
        dirFolders = list(iter_split(pathSplit[-1]))
        #dirFolders[0] = checkFolderName(dirFolders[0])
        dirFolders.insert(0, pathSplit[0])
    else:
        dirFolders = list(iter_split(winAbsPath))
    if "本機" not in dirFolders: dirFolders.insert(0, rootName)
    print("Parsing Complete: {}".format(dirFolders))
    return dirFolders

def getFolderObject_byAbsPath(absPath):
    print("Starting to get Object from absolute path: {}".format(absPath))
    dirFolders = parseAbsName(absPath)

    folderObject, _ = getFolderObject(dirFolders[0])
    folderObject, _ = getFolderObject(dirFolders[1]+":" if ":" in absPath else dirFolders[1], folderObject,  isWholeName=False)

    for folder in dirFolders[2:]:
        parentFolderObject = folderObject
        folderObject, folderPIDL = getFolderObject(folder, parentFolderObject)

    print("Success to get folder object.  ||  Object with path: [{}]\n".format(absPath))
    return folderObject, folderPIDL
    


def copyShellItem(srcFolderObject, dstFolderObject, itemPIDL, dstItemName=None):
    src_idl = shell.SHGetIDListFromObject(srcFolderObject) #Grab the PIDL from the folder object
    dst_idl = shell.SHGetIDListFromObject(dstFolderObject) #Grab the PIDL from the folder object

    src = shell.SHCreateShellItem(src_idl, None, itemPIDL) #Create a ShellItem of the source file
    dst = shell.SHCreateItemFromIDList(dst_idl)

    # Python IFileOperation
    pfo = pythoncom.CoCreateInstance(shell.CLSID_FileOperation,None,pythoncom.CLSCTX_ALL,shell.IID_IFileOperation)
    pfo.SetOperationFlags(shellcon.FOF_NOCONFIRMATION)
    pfo.CopyItem(src, dst, dstItemName) # Schedule an operation to be performed
    success = pfo.PerformOperations() #perform operation
    print("Copy Log: {}".format(success))


def copyShellItem_batch(srcFolderObject, dstFolderObject, itemPIDL_list:List, dstItemName=None, filtering=True):
    if filtering: filteringSignals = getFilteringSignals(srcFolderObject)
    src_idl = shell.SHGetIDListFromObject(srcFolderObject) #Grab the PIDL from the folder object
    dst_idl = shell.SHGetIDListFromObject(dstFolderObject) #Grab the PIDL from the folder object

    src_list = []
    if filtering:
        for pidl, isFiltered in zip(itemPIDL_list, filteringSignals):
            if isFiltered: src_list.append(shell.SHCreateShellItem(src_idl, None, pidl)) #Create a ShellItem of the source file
    else:
        for pidl in itemPIDL_list:
            src_list.append(shell.SHCreateShellItem(src_idl, None, pidl)) #Create a ShellItem of the source file

    # PerformOperations() 在零排程時會回 0x8000FFFF (E_UNEXPECTED)，
    # 也就是 -2147418113「災難性的失敗」。必須先擋下來。
    if not src_list:
        print("There are 0 files to be copied. [{}]. Skipped.".format(
            "Filtered" if filtering else "Not filtered, Copy All."))
        return

    dst = shell.SHCreateItemFromIDList(dst_idl)

    # Python IFileOperation
    pfo = pythoncom.CoCreateInstance(shell.CLSID_FileOperation,None,pythoncom.CLSCTX_ALL,shell.IID_IFileOperation)
    pfo.SetOperationFlags(shellcon.FOF_NOCONFIRMATION)
    print("Loading files to copy...")
    for src in tqdm(src_list):
        pfo.CopyItem(src, dst, dstItemName) # Schedule an operation to be performed
    print("There are {} files to be copied. [{}].".format(len(src_list), "Filtered" if filtering else "Not filtered, Copy All."))
    print("Starting Copying...")
    success = pfo.PerformOperations() #perform operation
    print("Copy Log: {}".format(success))

def getArguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('-f', '--fromPath', type=str, required=True ,help='Input the folder path of where your photos are. (to be copied)')
    parser.add_argument('-t', '--toPath', type=str, required=True ,help='Input the folder path of where your photos are going to copy to. (your backup storage, not iPhone)')
    parser.add_argument('--filter', type=int, required=False, default=True, help='Input 1 if you only want to copy images, otherwise input 0 to copy all. (Default: 1)')
    args = parser.parse_args()
    return args

def main():
    args = getArguments()
    fromPath = args.fromPath
    toPath = args.toPath
    filtering = args.filter
    print(filtering)
    sourceFolderObject, _ = getFolderObject_byAbsPath(fromPath)
    destinFolderObject, _ = getFolderObject_byAbsPath(toPath)
    copyShellItem_batch(sourceFolderObject, destinFolderObject, sourceFolderObject, filtering=filtering)

# python iphoneGetDrive_module.py -f C:\Users\pair\jackchang\111_1_Course\images -t C:\Users\pair\jackchang\111_1_Course\images_2 --filter 1


if __name__=="__main__":
    main()
    