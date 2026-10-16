'''

dir_path = ".\\log\\"
with open('.\\log\\test.log', 'r') as file:
  lines = file.readlines()
  answer = []
  for i in range(len(lines)):
    if i % 2 == 0:
      x, y = lines[i].split(':')
      x, y = x.split()[1], y.strip()
      answer.append({"time": float(x), "name": y})

  answer.sort(key=lambda x: x["name"])


import pandas as pd
def pd_toExcel(data, fileName):  # pandas库储存数据到excel
    times = []
    names = []
    for i in range(len(data)):
        times.append(data[i]["time"])
        names.append(data[i]["name"])
 
    dfData = {  # 用字典设置DataFrame所需数据
      'time': times,
      'name': names,
    }
    df = pd.DataFrame(dfData)  # 创建DataFrame
    df.to_excel(fileName, index=False)

fileName = ".\\data\\test.xlsx"
pd_toExcel(answer, fileName)

'''



import random
import math
#
#
# A_B = [-1, 1]
#
# def generateRandomWidth():
#   ms = random.randint(0,10)
#   v = 2
#   command = f"width id=random_1_1 s=1 sh=1 ms={ms} v={v}\n"
#   return command
# def generateRandomSlope():
#   x = [0, 1]
#   ms = random.randint(0,10)
#   v = 0.1
#   mv = random.choice(x)
#   command = f"slope id=random_1_1 ms={ms} v={v} ms={ms} mv={mv}\n"
#   return command
#
# def generateRandomCurve():
#   v0 = (1+random.random())*random.choice(A_B)/4
#   v1 = (1+random.random())*random.choice(A_B)/4
#   h0, h1 = 0, 0
#   if random.random() > 0.95:
#     h0 = (1+random.random())*random.choice(A_B)*math.pi/10
#     h1 = (1+random.random())*random.choice(A_B)*math.pi/10
#   command = f"curve id=random_1_1 v0={v0} v1={v1} h0={h0} h1={h1}\n"
#   return command
#
# filenames = [
#   "ARG_Carcarana-3_3_I-1-1",
#   "ARG_Carcarana-11_2_I-1-1",
#   "ARG_Carcarana-12_2_T-1",
#   "ARG_Carcarana-1_3_I-1-1",
#   "ARG_Carcarana-7_1_I-1-1",
#   "BEL_Zwevegem-1_2_T-1",
#   "BEL_Wervik-4_2_I-1-1",
#   "BEL_Putte-17_1_I-1-1",
#   "BEL_Putte-16_2_T-1",
#   "BEL_Zwevegem-3_3_T-1",
#   "BEL_Zwevegem-8_4_I-1-1",
#   "BEL_Zaventem-5_1_T-1",
#   "BEL_Zaventem-2_6_I-1-1",
#   "BEL_Nivelles-9_2_T-1",
#   "BEL_Nivelles-7_1_T-1",
#   "BEL_Putte-14_1_I-1-1",
#   "BEL_Nivelles-18_1_I-1-1",
#   "BEL_Aarschot-5_1_T-1",
#   "BEL_Nivelles-16_2_T-1",
#   "BEL_Zaventem-1_1_T-1",
#   "BEL_Putte-10_1_I-1-1",
#   "BEL_Zwevegem-1_1_T-1",
#   "BEL_Putte-5_5_T-1",
#   "BEL_Putte-2_2_I-1-1",
#   "BEL_Beringen-3_4_I-1-1",
#   "CHN_Sha-11_1_I-1-1",
#   "CHN_Sha-1_8_T-1",
#   "CHN_Sha-2_6_T-1",
#   "CHN_Sha-5_1_T-1",
#   "CHN_Sha-14_1_T-1",
#   "USA_Lanker-2_17_I-1-1"
# ]
#
# with open('.\\data\\script\\testSlope.odrScript', 'w', encoding='utf-8') as f:
#   size = len(filenames)
#   for i in range(size):
#     f.write(f"{filenames[i]}\n")
#     f.write(f"savename slope{i}\n")
#     f.write(generateRandomSlope())
#     f.write("close\n")
#
# print("OK")
import math
import time
#
# num=[99999,9999999999999999999,999]
# for n in num:
#   print(f"words{n:<30} {n:<30} {n:<30}")

def get_h(h,len):
  return math.tan(h * math.pi / 180) * len

# roads=[id,len,mv]
roads1=[
  [7,4.7,1],
  [11,12.16,0],
  [21,3.5,0],
  [28,9.26,0],
  [31,13.2,0],
  [35,4.54,0]
]
roads2=[
  [24,11,1],
  [29,11.06,1],
  [32,14.46,1],
  [36,6.8,1],
  [37,4.55,1],
  [39,12.96,1],
  [40,8.128,1],
]
def po(roads,h,num=99):
  value=0
  for i in range(len(roads)):
    road=roads[i]
    if i>=num:
      print(f"slope id={road[0]} v={value} mv=1 m=add")
    else:
      value = get_h(h,road[1])+value
      print(f"slope id={road[0]} v={value} mv=1 m=add")

  for i in range(len(roads)):
    print(f"undo")
  pass

roads3=[
  [19,-1],
  [24,-1],
  [30,-1],
  [23,-1],
  [28,-1],
]
# ESP_SantBoideLlobregat-8_2_I-1-1
roads4=[
  [1,-1],
  [3,-1],
  [5,-1],
]
def kuan(roads,value,):

  for i in range(len(roads)):
    road=roads[i]
    print(f"width id={road[0]} v=-{value/10.0} li={road[1]}")

  for i in range(len(roads)):
    print(f"undo")
  pass

# ho=h0
# hl=h1
# 24 30
nameid="24"
jsonname="DEU_AAH3-32_25_T-1"
mapname="DEU_AAH3"
print(mapname)
print(jsonname)
for i  in range(1,10):
  value = 0
  id = 24
  li=-1

  # i=-i
  # tailname=f"id_{nameid}_h0_15_v0_0_{i}_W_v_-1_1_li_-1"
  tailname=f"W_id_{nameid}_v_-{int(i/10)}_{i%10}_li_{li}"

  # tailname=f"id_{nameid}_h0_{i}_v0_0_4"
  print(f"savename {mapname}_{tailname}")
  print(f"jsonsavename {jsonname}_{tailname}")

  # 坡度=====================================================================
  # tailname=f"id_{nameid}_v_{i}_mv_1"

  # po(roads2, i)

  # po(roads1, i)

  # 曲线=====================================================================
  # print(f"curve id={id} h0={i} v0=0.4")
  # print(f"curve id={id} h0=15 v0={i/10.0}")
  # print(f"width id={id} v=-{i/10} li={li}")


  # value2 = math.tan(i * math.pi / 180) *38-1
  # print(f"slope id=19 v={value2} mv=1 m=add")
  # 宽度=====================================================================
  # print(f"width id={id} v=-{i/10} li={li}")
  # print(f"width id=9 v=-{i/10} li={li}")
  # print(f"width id={id} v={i/10.0} li=-1 s=1")
  # id=11
  print(f"width id={id} v={i/10.0} li=-1")
  # kuan(roads4, i)

  # print("undo")
  # print("undo")
  print("reinit")

print("close")
print("="*100)

