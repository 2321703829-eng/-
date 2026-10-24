from datetime import datetime
import time

logPath = ".\\data\\log\\"


class Logger:
    def __init__(self):
        self.logPath = logPath
        self.timeStamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.lastTime = None

    def countTime(self):
        self.lastTime = time.perf_counter()

    def write(self, command):
        try:
            with open(logPath + self.timeStamp + ".log", "a") as file:
                timeStamp = datetime.now().strftime("%Y-%m-%d_%H_%M_%S")
                nowTime = time.perf_counter()
                exeTime = nowTime - self.lastTime
                file.write(
                    timeStamp
                    + "		"
                    + str(exeTime).rjust(20, " ")
                    + ":		"
                    + command
                    + "\n"
                )
                return True

        except FileNotFoundError:
            print("Log Error: File not found!")
            return False


logger = Logger()
