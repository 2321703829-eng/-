saveName = None
jsonSaveName = None

xodrPath = "./data/OpenSCENARIO/"
jsonPath = "./data/LGSVL-VSE/"
savePath = "./data/output/first_demo/"
scriptPath = "./data/script/"


def editSaveName(newName):
    global saveName
    global jsonSaveName
    saveName = newName
    if not jsonSaveName:
        jsonSaveName = saveName


def editJsonSaveName(newName):
    global jsonSaveName
    jsonSaveName = newName
    return "xodrsavename: " + newName


def editPATH(newPath):
    global xodrPath
    xodrPath = newPath
