import random

from scenePicker.scenePicker import sPicker


def getRandomValue(string):
    valueList = string.split("_")

    if valueList[0] == "random":
        try:
            v = random.uniform(float(valueList[1]), float(valueList[2]))
            print("v=", v)
        except IndexError:
            print("The number of params is too small!")
            return 0
        except ValueError:
            print("The string cannot be transformed into a number!")
            return 0
        except Exception:
            print("Unknown error!")
            return 0
    else:
        try:
            v = float(valueList[0])
        except ValueError:
            print("The string cannot be transformed into a number!")
            return 0
        except Exception:
            print("Unknown error!")
            return 0
    return v


def getRandomInt(num):
    return random.randrange(0, num)


def getRandomId(id, lane=False, curve=False):
    ego, npc = 1, 0
    # 可以为random，表示任取测试范围内的一个
    idList = id.split("_")
    # 虽然写在getRandomId里，但是前面这种处理是对应的写明了id等于几的
    try:
        # 因为idList[0]可能是random，所以用try
        value = int(idList[0])
        if lane:
            return idList[0], []
        return idList[0]

    except ValueError:
        if idList[0] == "random":
            try:
                ego, npc = int(idList[1]), int(idList[2])
            except Exception:
                print("Invalid random id!")
                pass

            sPicker.setCandidates(ego, npc)
            if lane:
                rid, lid = sPicker.getRandomId2(curve)
                return rid, [lid]
            return sPicker.getRandomId1(curve)

        else:
            print("Invalid id! Choose default 0.")
            if lane:
                return "0", ["0"]
            return "0"
