colors = [
    "#e6194B",
    "#3cb44b",
    "#ffe119",
    "#4363d8",
    "#f58231",
    "#42d4f4",
    "#f032e6",
    "#fabed4",
    "#469990",
    "#dcbeff",
    "#9A6324",
    "#fffac8",
    "#800000",
    "#aaffc3",
    "#000075",
    "#a9a9a9",
    "#ffffff",
    "#000000",
]

import matplotlib.pyplot as plt
import random
import numpy
import utils.path as path


def random_color():
    return (random.random(), random.random(), random.random())


def showCurve(params):
    # return None
    plt.clf()
    t = numpy.linspace(0, 1, 10000)
    for i in range(len(params)):
        # print(params[i])
        bU, cU, dU, bV, cV, dV, aU, aV = params[i]
        u = aU + bU * t + cU * t**2 + dU * t**3
        v = aV + bV * t + cV * t**2 + dV * t**3
        plt.plot(u, v, color=colors[i])
        plt.axis("equal")
    # plt.show()
    plt.savefig("./data/outputPics/" + path.saveName + ".png", dpi=600)
    print("save to ./data/outputPics/" + path.saveName + ".png")
