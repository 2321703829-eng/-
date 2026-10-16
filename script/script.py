from utils.path import scriptPath


class scriptReader:

    def __init__(self):
        self.tail = ".txt"
        pass

    def read(self):
        name = input("input the name of " + self.tail + " file: ")
        print(scriptPath + name + self.tail)
        try:
            with open(scriptPath + name + self.tail, "r") as file:
                ans = []
                commands = file.readlines()
                print(commands)

                for command in commands:
                    ans.append(command.rstrip())
                    # 截止位置
                    if "close" in command.lower():
                        break

                return ans

        except FileNotFoundError:
            print("Script Error: File not found!")
            return []


script = scriptReader()
