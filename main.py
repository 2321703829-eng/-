
import utils.path as path

from xodrs.xodrParser 			import XParser
from jsons.jsonParser 			import JParser
# from llm.llmXHXF 					import chater
from llm.llmMaxKB 					import chater
from script.script					import script
from handler.handlerCurve 	import handlerC
from handler.handlerFit 		import handlerF
from handler.handlerLane 		import handlerL
from handler.handlerObject 	import handlerO
from handler.handlerSignal	import handlerSi
from handler.handlerSlope 	import handlerSl
from handler.handlerWidth 	import handlerW
from handler.handlerRandom import handlerR
from logger.logger 					import logger
import matplotlib.pyplot as plt


import time
temXodrName=""
temJsonName=""

if __name__ == '__main__':
	# logger = Logger()
	commandList = []

	# plt.ion()
	is_script=False
	while True:
		if commandList == []:

			# mode = input("Please choose the mode of interaction.\nInput 'C' for command line interaction.\nInput 'S' for reading command script.\nInput 'L' for asking the large language model.\nInput 'E' to exit.\n")
			mode = 'S'
			print("=="*50)
			print(f"current mode:{mode}")

			is_script = False
			if mode == 'S':
				commandList = script.read()
				if commandList == []:
					print("ERROR: commandList is empty.")
					continue
				is_script = True
			elif mode == 'L':
				# commandList = chater.translate()
				if commandList == []:
					print("ERROR: commandList is empty.")
					continue
			elif mode == 'E':
				plt.ioff()
				break
			elif mode != 'C':
				print("WRONG MODE!")
				continue
		
		# if commandList != []:
		# 	fileName = commandList[0]
		# 	fileName = fileName.lstrip("openxodr ")
		# 	commandList.pop(0)
		# 	jsonName = commandList[0]
		# 	jsonName = jsonName.lstrip("openjson ")
		# 	commandList.pop(0)
		# else:
		# 	fileName = input("Enter the xodr file path here: ")
		# 	fileName = fileName.lstrip("openxodr ")
		#
		# 	jsonName = input("Enter the json file path here: ")
		# 	jsonName = jsonName.lstrip("openjson ")
		if not commandList:
			xodrFileName = input("Enter the xodr file path here: ")
			if not xodrFileName.startswith("openxodr"):
				xodrFileName ="openxodr "+xodrFileName
			commandList.append(xodrFileName)

			jsonFileName =  input("Enter the json file path here: ")
			if not jsonFileName.startswith("openxodr"):
				jsonFileName ="openjson "+jsonFileName
			commandList.append(jsonFileName)

		logger.countTime()
		# if XParser.openXodr(fileName) == False:
		# 	raise "json read False"
		#
		# if JParser.readJson(jsonName) == False:
		# 	raise "json read False"
		#
		# # 同时修改xodr和json的保存名
		# path.editSaveName(jsonName)
		# logger.write("open xodr:"+fileName)

		while True:
			if commandList != []:
				command = commandList[0].split()
				commandList.pop(0)
			else:
				if is_script:
					break
				command = input().split()
				if command == []:
					continue
			if len(command) > 1:
				print(f"正在执行：{command}")
			match command[0].lower():
				case "openxodr":
					temXodrName = command[1]
					if XParser.openXodr(command[1]) == False:
						logger.write(f"openxodr False: {command[1]}")
						raise "xodr read False"
					logger.write(f"openxodr: {command[1]}")
				case "openjson":
					temJsonName = command[1]
					if JParser.readJson(command[1]) == False:
						logger.write(f"openjson False: {command[1]}")
						raise "json read False"
					logger.write(f"openjson: {command[1]}")
				case "close":
					plt.close()
					print("Closed.")
					commandList = []
					break
				# 数据操作形式为累计操作，使用reinit回到文件读取后的最初状态
				case "reinit":
					XParser.reInit()
					JParser.reInit()
				# 数据操作形式为累计操作，所以当不想上一条命令影响到后续的命令时，需要先执行undo，从而回退到上一条命令执行后的状态
				case "undo":
					XParser.undo()
					JParser.undo()

				case "random":
					commandList = handlerR.handle(command)+commandList
					# for command in commandList:
					# 	print(command)
					# commandList.append("close")
					# commandList.clear()
					logger.write("Use Random")
				case "xodrsavename":
					path.editSaveName(command[1])
					# 修改json中保存的相关xodr文件名
					string=JParser.editSaveName(command[1])
					logger.write(string)
					print("Save name is now: ", path.saveName)
				case "jsonsavename":
					string=path.editJsonSaveName(command[1])
					logger.write(string)
					print("Save JsonSaveName is now: ", path.jsonSaveName)
				case "curve":
					logger.countTime()
					string = handlerC.handle(command)
					XParser.writeXodr()
					JParser.writeJson()
					logger.write(string)

				case "fit":
					logger.countTime()
					string = handlerF.handle(command)
					XParser.writeXodr()
					JParser.writeJson()
					logger.write(string)

				case "lane":
					logger.countTime()
					string = handlerL.handle(command)
					XParser.writeXodr()
					JParser.writeJson()
					logger.write(string)
				
				case "object":
					logger.countTime()
					string = handlerO.handle(command)
					XParser.writeXodr()
					JParser.writeJson()
					logger.write(string)

				case "signal":
					logger.countTime()
					string = handlerSi.handle(command)
					XParser.writeXodr()
					JParser.writeJson()
					logger.write(string)
		
				case "slope":
					logger.countTime()
					string = handlerSl.handle(command)
					print(string)
					XParser.writeXodr()
					JParser.writeJson()
					logger.write(string)

				case "width":
					logger.countTime()
					string = handlerW.handle(command)
					XParser.writeXodr()
					JParser.writeJson()
					logger.write(string)
				case _:
					print("Illegal command!")
