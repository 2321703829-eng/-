from llm.manager_agent import ManagerAgent
from llm.single_agent import SingleAgent
import time

if __name__ == "__main__":
    questions =[
        # "介绍你自己",
        "The starting point of Road 1 is too high, lower it by 2 meters.",# "1号道路的起点太高了，把它降低2米",
        "Widen Road 6 by 0.5 meters and increase its slope slightly.",# "将6号道路的宽度加宽0.5m，坡度也抬高一些",
        "Narrow the width of both Road 5 and Road 6, and smooth out the connection between them.",# "将5号道路与6号道路的宽度都缩减一些，并且将连接处处理得平滑一些",

        "Narrow the width of Roads 3 to 5 by 0.3 meters. Then raise the elevation of these three roads as a whole.", # "将3号至5号道路的路宽全部缩窄0.3m。之后整体抬高这三条道路",
        "Increase the slope of Road 6 and the two subsequent roads in the same direction,",# "抬高6号道路及其之后同向的2条道路的坡度",

        "Narrow Roads 3 to 5 by 0.3 meters each, and additionally raise the slope of Road 4 slightly more than the others.",# "将3至5号道路都缩窄0.3m，并且在此基础上，单独将4号道路的坡度再抬高一些。",

        "Increase the initial curvature of Road 15 and narrow Road 3 by 0.3m. Additionally, raise the end of Road 4 by 3m, and apply the same treatment to Road 5 as to Road 4.",
        # "将15号道路的起始弯曲度增大一些，并将3号道路缩窄0.3m。另外，将4号道路的末端抬高3m，5号道路也和4号一样进行处理"



        # "1号道路的起点太高了，把它降低2米"
        # "将6号道路的宽度加宽0.5m，坡度也抬高一些",
        # "将5号道路与6号道路的宽度都缩减一些，并且将连接处处理得平滑一些",
        # "将3号至5号道路的路宽全部缩窄0.3m。之后整体抬高这三条道路",
        # "抬高6号道路及其之后同向的2条道路的坡度",
        # "将3至5号道路都缩窄0.3m，并且在此基础上，单独将4号道路的坡度再抬高一些。",
        # "将15号道路的起始弯曲度增大一些，并将3号道路缩窄0.3m。另外，将4号道路的末端抬高3m，5号道路也和4号一样进行处理"




        # "12号道路的宽度有点宽了，坡度也调整的高一些",
        # "将3至5号道路都缩窄0.3m",
        # "将4号道路的末端抬高3m，-1号车道的宽度加宽0.2m，5号道路和4号进行一样的处理",
        # "抬高6号道路及其之后同向的5条道路的坡度",
        # "整体抬高6号道路及其之后同向的5条道路"
        # "将15号道路的起始弯曲度增大一些"
    ]

    # model_name="deepseek"
    model_name="GPT"
    # model_name="llama"
    # model_name="gemini"


    # Manager = ManagerAgent("zhipu")
    # Manager = ManagerAgent("gemini")
    # Manager = ManagerAgent("sparkLite")
    # Manager = ManagerAgent("cerebras")
    # Manager = ManagerAgent("TYQW")

    Manager = ManagerAgent(model_name)
    Single=SingleAgent(model_name)
    print(f"Current Model:{model_name}")
    for index in range(len(questions)):
        q=questions[index]
        print(f"Question:{q}")
        print("Multi-agent:")
        result=Manager.get_commands(q)
        print(result)
        Manager.dispatch_task(result["commands"])
        print("Single_Agent:")
        print(Single.get_commends(q))
        print("="*65)
        if  (index==2 or index==4  or index==5):
            time.sleep(60)




"""
2025年8月25日
Question:12号道路的宽度有点宽了，坡度也调整的高一些
Multi-agent:
{'commands': [{'type_name': 'width', 'description': '将12号道路的路面宽度减小0.5米'}, {'type_name': 'slope', 'description': '将12号道路的坡度增大5度，修改模式为angle，修改位置为首尾同时改变(mv=2)'}]}
widthAgent: 将12号道路的路面宽度减小0.5米
{'commands': 'width id=12 v=-0.5'}
slopeAgent: 将12号道路的坡度增大5度，修改模式为angle，修改位置为首尾同时改变(mv=2)
{'commands': 'slope id=12 v=5 m=angle mv=2'}
commandsList: [{'commands': 'width id=12 v=-0.5'}, {'commands': 'slope id=12 v=5 m=angle mv=2'}]
=================================================================
Question:将3至5号道路都缩窄0.3m
Multi-agent:
{'commands': [{'type_name': 'width', 'description': '将3号道路的路面宽度减小0.3米'}, {'type_name': 'width', 'description': '将4号道路的路面宽度减小0.3米'}, {'type_name': 'width', 'description': '将5号道路的路面宽度减小0.3米'}]}
widthAgent: 将3号道路的路面宽度减小0.3米
{'commands': 'width id=3 v=-0.3'}
widthAgent: 将4号道路的路面宽度减小0.3米
{'commands': 'width id=4 v=-0.3'}
widthAgent: 将5号道路的路面宽度减小0.3米
{'commands': 'width id=5 v=-0.3'}
commandsList: [{'commands': 'width id=3 v=-0.3'}, {'commands': 'width id=4 v=-0.3'}, {'commands': 'width id=5 v=-0.3'}]
=================================================================
Question:将4号道路的末端抬高3m，-1号车道的宽度加宽0.2m，5号道路和4号进行一样的处理
Multi-agent:
{'commands': [{'type_name': 'slope', 'description': '将4号道路的末端抬高3m，使用add模式修改，mv=1（道路末端）'}, {'type_name': 'width', 'description': '将4号道路的-1号车道宽度增加0.2m，li=-1'}, {'type_name': 'slope', 'description': '将5号道路的末端抬高3m，使用add模式修改，mv=1（道路末端）'}, {'type_name': 'width', 'description': '将5号道路的-1号车道宽度增加0.2m，li=-1'}]}
slopeAgent: 将4号道路的末端抬高3m，使用add模式修改，mv=1（道路末端）
{'commands': 'slope id=4 v=3 mv=1 m=add'}
widthAgent: 将4号道路的-1号车道宽度增加0.2m，li=-1
{'commands': 'width id=4 v=0.2 li=-1'}
slopeAgent: 将5号道路的末端抬高3m，使用add模式修改，mv=1（道路末端）
{'commands': 'slope id=5 v=3 m=add mv=1'}
widthAgent: 将5号道路的-1号车道宽度增加0.2m，li=-1
{'commands': 'width id=5 v=0.2 li=-1'}
commandsList: [{'commands': 'slope id=4 v=3 mv=1 m=add'}, {'commands': 'width id=4 v=0.2 li=-1'}, {'commands': 'slope id=5 v=3 m=add mv=1'}, {'commands': 'width id=5 v=0.2 li=-1'}]
=================================================================
Question:抬高6号道路及其之后同向的5条道路的坡度
Multi-agent:
{'commands': [{'type_name': 'slope', 'description': '将6号道路的坡度抬高5度，修改模式为angle，修改端为head（道路末端），广度优先搜索层数ms=5，仅在同向道路上搜索sh=1'}]}
slopeAgent: 将6号道路的坡度抬高5度，修改模式为angle，修改端为head（道路末端），广度优先搜索层数ms=5，仅在同向道路上搜索sh=1
{'commands': 'slope id=6 v=5 m=angle mv=1 ms=5 sh=1'}
commandsList: [{'commands': 'slope id=6 v=5 m=angle mv=1 ms=5 sh=1'}]
=================================================================
Question:整体抬高6号道路及其之后同向的5条道路
Multi-agent:
{'commands': [{'type_name': 'slope', 'description': '将6号道路整体抬高，修改模式为add，抬高量为1米，修改端为2（首尾同时改变），广度优先搜索层数ms=5，仅在同向道路上搜索sh=1'}]}
slopeAgent: 将6号道路整体抬高，修改模式为add，抬高量为1米，修改端为2（首尾同时改变），广度优先搜索层数ms=5，仅在同向道路上搜索sh=1
{'commands': 'slope id=6 v=1 m=add mv=2 ms=5 sh=1'}
commandsList: [{'commands': 'slope id=6 v=1 m=add mv=2 ms=5 sh=1'}]
=================================================================
Question:将15号道路的起始弯曲度增大一些
Multi-agent:
{'commands': [{'type_name': 'curve', 'description': '将15号道路的参数v0增大0.2（增大起始弯曲度）'}]}
curveAgent: 将15号道路的参数v0增大0.2（增大起始弯曲度）
{'commands': 'curve id=15 v0=0.2'}
commandsList: [{'commands': 'curve id=15 v0=0.2'}]
=================================================================


                An example:the requirement is "Raise the end of Road 4 by 3 m, widen Lane 1 by 0.2 m, and apply the same treatment to Road 5 and Road 4.", you break it down and return 
                {'commands': ['width id=12 v=-0.5', 'slope id=12 v=5 m=angle mv=2']}
                
                An example:the requirement is "Narrow roads 3 to 5 by 0.3 m.", you break it down and return 
                {'commands': ['width id=3 v=-0.3', 'width id=4 v=-0.3','width id=5 v=-0.3']}
                
                An example:the requirement is"Raise the end of Road 4 by 3 m, widen Lane 1 by 0.2 m, and apply the same treatment to Road 5 and Road 4.", you break it down and return 
                {'commands': ['slope id=4 v=3 mv=1 m=add','width id=4 v=0.2 li=-1','slope id=5 v=3 m=add mv=1','width id=5 v=0.2 li=-1']}
                
                An example:the requirement is"Raise the slope of Road No. 6 and the five roads in the same direction after it.", you break it down and return 
                {'commands': ['slope id=6 v=5 m=angle mv=1 ms=5 sh=1']}
                
                An example:the requirement is"Raise Road No. 6 and the five roads in the same direction after it as a whole.", you break it down and return
                {'commands': ['slope id=6 v=1 m=add mv=2 ms=5 sh=1']}
                
                An example:the requirement is"Increase the initial curvature of Road 15.", you break it down and return
                {'commands': ['curve id=15 v0=0.2']}
"""


"""
Current Model:deepseek
Question:The starting point of Road 1 is too high; lower it by 2 meters.
Multi-agent:
{'commands': [{'type_name': 'slope', 'description': 'Lower the starting point of Road 1 by 2 meters, modify using add mode, mv=0 (road start)'}]}
slopeAgent: Lower the starting point of Road 1 by 2 meters, modify using add mode, mv=0 (road start)
{'command': 'slope id=1 v=-2 m=add mv=0'}
commandsList: [{'command': 'slope id=1 v=-2 m=add mv=0'}]
Single_Agent:
{'commands': [{'command': 'slope id=1 v=-2 m=add mv=0'}]}
=================================================================
Question:Widen Road 6 by 0.5 meters and increase its slope slightly.
Multi-agent:
{'commands': [{'type_name': 'width', 'description': 'Increase the width of Road 6 by 0.5 meters.'}, {'type_name': 'slope', 'description': 'Increase the slope of Road 6 by 2 degrees, change the mode to angle, and change the position to the end of the road (mv=1).'}]}
widthAgent: Increase the width of Road 6 by 0.5 meters.
{'command': 'width id=6 v=0.5'}
slopeAgent: Increase the slope of Road 6 by 2 degrees, change the mode to angle, and change the position to the end of the road (mv=1).
{'command': 'slope id=6 v=2 m=angle mv=1'}
commandsList: [{'command': 'width id=6 v=0.5'}, {'command': 'slope id=6 v=2 m=angle mv=1'}]
Single_Agent:
{'commands': [{'command': 'width id=6 v=0.5'}, {'command': 'slope id=6 v=5 m=angle mv=2'}]}
=================================================================
Question:Narrow the width of both Road 5 and Road 6, and smooth out the connection between them.
Multi-agent:
{'commands': [{'type_name': 'width', 'description': 'Reduce the width of Road 5 by 0.5 meters, with smooth connection to other roads (s=1).'}, {'type_name': 'width', 'description': 'Reduce the width of Road 6 by 0.5 meters, with smooth connection to other roads (s=1).'}]}
widthAgent: Reduce the width of Road 5 by 0.5 meters, with smooth connection to other roads (s=1).
{'command': 'width id=5 v=-0.5 s=1'}
widthAgent: Reduce the width of Road 6 by 0.5 meters, with smooth connection to other roads (s=1).
{'command': 'width id=6 v=-0.5 s=1'}
commandsList: [{'command': 'width id=5 v=-0.5 s=1'}, {'command': 'width id=6 v=-0.5 s=1'}]
Single_Agent:
{'commands': [{'command': 'width id=5 v=-0.3 s=1'}, {'command': 'width id=6 v=-0.3 s=1'}]}
=================================================================
Question:Narrow the width of Roads 3 to 5 by 0.3 meters. Then raise the elevation of these three roads as a whole,
Multi-agent:
{'commands': [{'type_name': 'width', 'description': 'Reduce the width of Road No.3 by 0.3 meters.'}, {'type_name': 'width', 'description': 'Reduce the width of Road No.4 by 0.3 meters.'}, {'type_name': 'width', 'description': 'Reduce the width of Road No.5 by 0.3 meters.'}, {'type_name': 'slope', 'description': 'Raise Road 3 as a whole, change the mode to add, raise by 1 meter, both ends change simultaneously (mv=2).'}, {'type_name': 'slope', 'description': 'Raise Road 4 as a whole, change the mode to add, raise by 1 meter, both ends change simultaneously (mv=2).'}, {'type_name': 'slope', 'description': 'Raise Road 5 as a whole, change the mode to add, raise by 1 meter, both ends change simultaneously (mv=2).'}]}
widthAgent: Reduce the width of Road No.3 by 0.3 meters.
{'command': 'width id=3 v=-0.3'}
widthAgent: Reduce the width of Road No.4 by 0.3 meters.
{'command': 'width id=4 v=-0.3'}
widthAgent: Reduce the width of Road No.5 by 0.3 meters.
{'command': 'width id=5 v=-0.3'}
slopeAgent: Raise Road 3 as a whole, change the mode to add, raise by 1 meter, both ends change simultaneously (mv=2).
{'command': 'slope id=3 v=1 m=add mv=2'}
slopeAgent: Raise Road 4 as a whole, change the mode to add, raise by 1 meter, both ends change simultaneously (mv=2).
{'command': 'slope id=4 v=1 m=add mv=2'}
slopeAgent: Raise Road 5 as a whole, change the mode to add, raise by 1 meter, both ends change simultaneously (mv=2).
{'command': 'slope id=5 v=1 m=add mv=2'}
commandsList: [{'command': 'width id=3 v=-0.3'}, {'command': 'width id=4 v=-0.3'}, {'command': 'width id=5 v=-0.3'}, {'command': 'slope id=3 v=1 m=add mv=2'}, {'command': 'slope id=4 v=1 m=add mv=2'}, {'command': 'slope id=5 v=1 m=add mv=2'}]
Single_Agent:
{'commands': [{'command': 'width id=3 v=-0.3'}, {'command': 'width id=4 v=-0.3'}, {'command': 'width id=5 v=-0.3'}, {'command': 'slope id=3 v=1 m=add mv=2 ms=2 sh=1'}]}
=================================================================
Question:Increase the slope of Road 6 and the two subsequent roads in the same direction,
Multi-agent:
{'commands': [{'type_name': 'slope', 'description': 'Increase the slope of Road 6 by 5 degrees, change the mode to angle, change the road end (mv=1), breadth-first search depth (ms=2), search only on roads in the same direction (sh=1)'}]}
slopeAgent: Increase the slope of Road 6 by 5 degrees, change the mode to angle, change the road end (mv=1), breadth-first search depth (ms=2), search only on roads in the same direction (sh=1)
{'command': 'slope id=6 v=5 m=angle mv=1 ms=2 sh=1'}
commandsList: [{'command': 'slope id=6 v=5 m=angle mv=1 ms=2 sh=1'}]
Single_Agent:
{'commands': [{'command': 'slope id=6 v=5 m=angle mv=2 ms=2 sh=1'}]}
=================================================================
Question:Narrow Roads 3 to 5 by 0.3 meters each, and additionally raise the slope of Road 4 slightly more than the others,
Multi-agent:
{'commands': [{'type_name': 'width', 'description': 'Reduce the width of Road No.3 by 0.3 meters.'}, {'type_name': 'width', 'description': 'Reduce the width of Road No.4 by 0.3 meters.'}, {'type_name': 'width', 'description': 'Reduce the width of Road No.5 by 0.3 meters.'}, {'type_name': 'slope', 'description': 'Increase the slope of Road No.4 by 3 degrees, change the mode to angle, and change the position to the end of the road (mv=1).'}]}
widthAgent: Reduce the width of Road No.3 by 0.3 meters.
{'command': 'width id=3 v=-0.3'}
widthAgent: Reduce the width of Road No.4 by 0.3 meters.
{'command': 'width id=4 v=-0.3'}
widthAgent: Reduce the width of Road No.5 by 0.3 meters.
{'command': 'width id=5 v=-0.3'}
slopeAgent: Increase the slope of Road No.4 by 3 degrees, change the mode to angle, and change the position to the end of the road (mv=1).
{'command': 'slope id=4 v=3 m=angle mv=1'}
commandsList: [{'command': 'width id=3 v=-0.3'}, {'command': 'width id=4 v=-0.3'}, {'command': 'width id=5 v=-0.3'}, {'command': 'slope id=4 v=3 m=angle mv=1'}]
Single_Agent:
{'commands': [{'command': 'width id=3 v=-0.3'}, {'command': 'width id=4 v=-0.3'}, {'command': 'width id=5 v=-0.3'}, {'command': 'slope id=4 v=random_2_5 m=angle mv=2'}]}
=================================================================
Question:Increase the initial curvature of Road 15 and narrow Road 3 by 0.3m. Additionally, raise the end of Road 4 by 3m, and apply the same treatment to Road 5 as to Road 4.
Multi-agent:
{'commands': [{'type_name': 'curve', 'description': 'Increase the initial curvature of Road 15 by increasing parameter v0 by 0.2'}, {'type_name': 'width', 'description': 'Reduce the width of Road 3 by 0.3 m'}, {'type_name': 'slope', 'description': 'Raise the end of Road 4 by 3 m, modify using add mode, mv=1 (road end)'}, {'type_name': 'slope', 'description': 'Raise the end of Road 5 by 3 m, modify using add mode, mv=1 (road end)'}]}
curveAgent: Increase the initial curvature of Road 15 by increasing parameter v0 by 0.2
{'command': 'curve id=15 v0=0.2'}
widthAgent: Reduce the width of Road 3 by 0.3 m
{'command': 'width id=3 v=-0.3'}
slopeAgent: Raise the end of Road 4 by 3 m, modify using add mode, mv=1 (road end)
{'command': 'slope id=4 v=3 m=add mv=1'}
slopeAgent: Raise the end of Road 5 by 3 m, modify using add mode, mv=1 (road end)
{'command': 'slope id=5 v=3 m=add mv=1'}
commandsList: [{'command': 'curve id=15 v0=0.2'}, {'command': 'width id=3 v=-0.3'}, {'command': 'slope id=4 v=3 m=add mv=1'}, {'command': 'slope id=5 v=3 m=add mv=1'}]
Single_Agent:
{'commands': [{'command': 'curve id=15 v0=0.2'}, {'command': 'width id=3 v=-0.3'}, {'command': 'slope id=4 v=3 m=add mv=1'}, {'command': 'slope id=5 v=3 m=add mv=1'}]}
=================================================================





Current Model:GPT
Question:The starting point of Road 1 is too high, lower it by 2 meters.
Multi-agent:
{'commands': [{'type_name': 'slope', 'description': 'Lower the starting point of Road 1 by 2 meters, modify using add mode, mv=0 (road start).'}]}
slopeAgent: Lower the starting point of Road 1 by 2 meters, modify using add mode, mv=0 (road start).
{'command': 'slope id=1 v=-2 m=add mv=0'}
commandsList: [{'command': 'slope id=1 v=-2 m=add mv=0'}]
Single_Agent:
{'commands': [{'command': 'slope id=1 v=-2 m=add mv=0'}]}
=================================================================
Question:Widen Road 6 by 0.5 meters and increase its slope slightly.
Multi-agent:
{'commands': [{'type_name': 'width', 'description': 'Increase the width of Road No.6 by 0.5 meters.'}, {'type_name': 'slope', 'description': 'Increase the slope of Road No.6 by 0.5 degrees, change the mode to angle.'}]}
widthAgent: Increase the width of Road No.6 by 0.5 meters.
{'command': 'width id=6 v=0.5'}
slopeAgent: Increase the slope of Road No.6 by 0.5 degrees, change the mode to angle.
{'command': 'slope id=6 v=0.5 m=angle'}
commandsList: [{'command': 'width id=6 v=0.5'}, {'command': 'slope id=6 v=0.5 m=angle'}]
Single_Agent:
{'commands': [{'command': 'width id=6 v=0.5'}, {'command': 'slope id=6 v=random_0.05_0.1 m=angle mv=2'}]}
=================================================================
Question:Narrow the width of both Road 5 and Road 6, and smooth out the connection between them.
Multi-agent:
{'commands': [{'type_name': 'width', 'description': 'Reduce the width of Road No.5 by 0.5 meters and enable smooth connection (s=1).'}, {'type_name': 'width', 'description': 'Reduce the width of Road No.6 by 0.5 meters and enable smooth connection (s=1).'}]}
widthAgent: Reduce the width of Road No.5 by 0.5 meters and enable smooth connection (s=1).
{'command': 'width id=5 v=-0.5 s=1'}
widthAgent: Reduce the width of Road No.6 by 0.5 meters and enable smooth connection (s=1).
{'command': 'width id=6 v=-0.5 s=1'}
commandsList: [{'command': 'width id=5 v=-0.5 s=1'}, {'command': 'width id=6 v=-0.5 s=1'}]
Single_Agent:
{'commands': [{'command': 'width id=5 v=-0.5 s=1'}, {'command': 'width id=6 v=-0.5 s=1'}]}
=================================================================
Question:Narrow the width of Roads 3 to 5 by 0.3 meters. Then raise the elevation of these three roads as a whole,
Multi-agent:
{'commands': [{'type_name': 'width', 'description': 'Reduce the width of Road No.3 by 0.3 meters.'}, {'type_name': 'width', 'description': 'Reduce the width of Road No.4 by 0.3 meters.'}, {'type_name': 'width', 'description': 'Reduce the width of Road No.5 by 0.3 meters.'}, {'type_name': 'slope', 'description': 'Raise the elevation of Road No.3 by 1 meter, modify using add mode, change both ends (mv=2).'}, {'type_name': 'slope', 'description': 'Raise the elevation of Road No.4 by 1 meter, modify using add mode, change both ends (mv=2).'}, {'type_name': 'slope', 'description': 'Raise the elevation of Road No.5 by 1 meter, modify using add mode, change both ends (mv=2).'}]}
widthAgent: Reduce the width of Road No.3 by 0.3 meters.
{'command': 'width id=3 v=-0.3'}
widthAgent: Reduce the width of Road No.4 by 0.3 meters.
{'command': 'width id=4 v=-0.3'}
widthAgent: Reduce the width of Road No.5 by 0.3 meters.
{'command': 'width id=5 v=-0.3'}
slopeAgent: Raise the elevation of Road No.3 by 1 meter, modify using add mode, change both ends (mv=2).
{'command': 'slope id=3 v=1 m=add mv=2'}
slopeAgent: Raise the elevation of Road No.4 by 1 meter, modify using add mode, change both ends (mv=2).
{'command': 'slope id=4 v=1 m=add mv=2'}
slopeAgent: Raise the elevation of Road No.5 by 1 meter, modify using add mode, change both ends (mv=2).
{'command': 'slope id=5 v=1 m=add mv=2'}
commandsList: [{'command': 'width id=3 v=-0.3'}, {'command': 'width id=4 v=-0.3'}, {'command': 'width id=5 v=-0.3'}, {'command': 'slope id=3 v=1 m=add mv=2'}, {'command': 'slope id=4 v=1 m=add mv=2'}, {'command': 'slope id=5 v=1 m=add mv=2'}]
Single_Agent:
{'commands': [{'command': 'width id=3 v=-0.3'}, {'command': 'width id=4 v=-0.3'}, {'command': 'width id=5 v=-0.3'}, {'command': 'slope id=3 v=1 m=add mv=2'}, {'command': 'slope id=4 v=1 m=add mv=2'}, {'command': 'slope id=5 v=1 m=add mv=2'}]}
=================================================================
Question:Increase the slope of Road 6 and the two subsequent roads in the same direction,
Multi-agent:
{'commands': [{'type_name': 'slope', 'description': 'Increase the slope of Road 6 by 3 degrees, change the mode to angle, change the road end (mv=1), breadth-first search layer (ms=2), search only on roads in the same direction (sh=1)'}]}
slopeAgent: Increase the slope of Road 6 by 3 degrees, change the mode to angle, change the road end (mv=1), breadth-first search layer (ms=2), search only on roads in the same direction (sh=1)
{'command': 'slope id=6 v=3 m=angle mv=1 ms=2 sh=1'}
commandsList: [{'command': 'slope id=6 v=3 m=angle mv=1 ms=2 sh=1'}]
Single_Agent:
{'commands': [{'command': 'slope id=6 v=5 m=angle mv=1 ms=2 sh=1'}]}
=================================================================
Question:Narrow Roads 3 to 5 by 0.3 meters each, and additionally raise the slope of Road 4 slightly more than the others,
Multi-agent:
{'commands': [{'type_name': 'width', 'description': 'Reduce the width of Road No.3 by 0.3 meters.'}, {'type_name': 'width', 'description': 'Reduce the width of Road No.4 by 0.3 meters.'}, {'type_name': 'width', 'description': 'Reduce the width of Road No.5 by 0.3 meters.'}, {'type_name': 'slope', 'description': 'Increase the slope of Road No.4 by 1 degree, change the mode to angle, and apply the change at the road end (mv=1).'}]}
widthAgent: Reduce the width of Road No.3 by 0.3 meters.
{'command': 'width id=3 v=-0.3'}
widthAgent: Reduce the width of Road No.4 by 0.3 meters.
{'command': 'width id=4 v=-0.3'}
widthAgent: Reduce the width of Road No.5 by 0.3 meters.
{'command': 'width id=5 v=-0.3'}
slopeAgent: Increase the slope of Road No.4 by 1 degree, change the mode to angle, and apply the change at the road end (mv=1).
{'command': 'slope id=4 v=1 m=angle mv=1'}
commandsList: [{'command': 'width id=3 v=-0.3'}, {'command': 'width id=4 v=-0.3'}, {'command': 'width id=5 v=-0.3'}, {'command': 'slope id=4 v=1 m=angle mv=1'}]
Single_Agent:
{'commands': [{'command': 'width id=3 v=-0.3'}, {'command': 'width id=4 v=-0.3'}, {'command': 'width id=5 v=-0.3'}, {'command': 'slope id=3 v=random_0.2_0.3 m=angle mv=1'}, {'command': 'slope id=4 v=random_0.4_0.6 m=angle mv=1'}, {'command': 'slope id=5 v=random_0.2_0.3 m=angle mv=1'}]}
=================================================================
Question:Increase the initial curvature of Road 15 and narrow Road 3 by 0.3m. Additionally, raise the end of Road 4 by 3m, and apply the same treatment to Road 5 as to Road 4.
Multi-agent:
{'commands': [{'type_name': 'curve', 'description': 'Increase the initial curvature of Road 15 by increasing v0 by 0.2.'}, {'type_name': 'width', 'description': 'Reduce the width of Road 3 by 0.3 meters.'}, {'type_name': 'slope', 'description': 'Raise the end of Road 4 by 3 meters, using add mode, mv=1 (road end).'}, {'type_name': 'slope', 'description': 'Raise the end of Road 5 by 3 meters, using add mode, mv=1 (road end).'}]}
curveAgent: Increase the initial curvature of Road 15 by increasing v0 by 0.2.
{'command': 'curve id=15 v0=0.2'}
widthAgent: Reduce the width of Road 3 by 0.3 meters.
{'command': 'width id=3 v=-0.3'}
slopeAgent: Raise the end of Road 4 by 3 meters, using add mode, mv=1 (road end).
{'command': 'slope id=4 v=3 mv=1 m=add'}
slopeAgent: Raise the end of Road 5 by 3 meters, using add mode, mv=1 (road end).
{'command': 'slope id=5 v=3 m=add mv=1'}
commandsList: [{'command': 'curve id=15 v0=0.2'}, {'command': 'width id=3 v=-0.3'}, {'command': 'slope id=4 v=3 mv=1 m=add'}, {'command': 'slope id=5 v=3 m=add mv=1'}]
Single_Agent:
{'commands': [{'command': 'curve id=15 v0=0.2'}, {'command': 'width id=3 v=-0.3'}, {'command': 'slope id=4 v=3 mv=1 m=add'}, {'command': 'slope id=5 v=3 m=add mv=1'}]}
=================================================================





Current Model:llama
Question:The starting point of Road 1 is too high, lower it by 2 meters.
Multi-agent:
{'commands': [{'type_name': 'slope', 'description': 'Lower the starting point of Road 1 by 2 meters, modify using add mode, change the position to the start of the road (mv=0).'}]}
slopeAgent: Lower the starting point of Road 1 by 2 meters, modify using add mode, change the position to the start of the road (mv=0).
{'command': 'slope id=1 v=-2 m=add mv=0'}
commandsList: [{'command': 'slope id=1 v=-2 m=add mv=0'}]
Single_Agent:
{'commands': [{'command': 'slope id=1 v=-2 m=add mv=0'}]}
=================================================================
Question:Widen Road 6 by 0.5 meters and increase its slope slightly.
Multi-agent:
{'commands': [{'type_name': 'width', 'description': 'Increase the width of Road 6 by 0.5 meters.'}, {'type_name': 'slope', 'description': 'Increase the slope of Road 6 slightly, change the mode to angle.'}]}
widthAgent: Increase the width of Road 6 by 0.5 meters.
{'command': 'width id=6 v=0.5'}
slopeAgent: Increase the slope of Road 6 slightly, change the mode to angle.
{'command': 'slope id=6 v=2 m=angle'}
commandsList: [{'command': 'width id=6 v=0.5'}, {'command': 'slope id=6 v=2 m=angle'}]
Single_Agent:
{'commands': [{'command': 'width id=6 v=0.5'}, {'command': 'slope id=6 v=1 m=angle'}]}
=================================================================
Question:Narrow the width of both Road 5 and Road 6, and smooth out the connection between them.
Multi-agent:
{'commands': [{'type_name': 'width', 'description': 'Reduce the width of Road 5, and make the edge connecting to other roads smooth.'}, {'type_name': 'width', 'description': 'Reduce the width of Road 6, and make the edge connecting to other roads smooth.'}]}
widthAgent: Reduce the width of Road 5, and make the edge connecting to other roads smooth.
{'command': 'width id=5 v=-0 s=1'}
widthAgent: Reduce the width of Road 6, and make the edge connecting to other roads smooth.
{'command': 'width id=6 v=-0 s=1'}
commandsList: [{'command': 'width id=5 v=-0 s=1'}, {'command': 'width id=6 v=-0 s=1'}]
Single_Agent:
{'commands': [{'command': 'width id=5 v=-0.3 s=1'}, {'command': 'width id=6 v=-0.3 s=1'}]}
=================================================================
Question:Narrow the width of Roads 3 to 5 by 0.3 meters. Then raise the elevation of these three roads as a whole,
Multi-agent:
{'commands': [{'type_name': 'width', 'description': 'Reduce the width of Road No.3 by 0.3 meters.'}, {'type_name': 'width', 'description': 'Reduce the width of Road No.4 by 0.3 meters.'}, {'type_name': 'width', 'description': 'Reduce the width of Road No.5 by 0.3 meters.'}, {'type_name': 'slope', 'description': 'Raise Roads 3, 4, and 5 as a whole, change the mode to add, raise by 0.3 meters, both ends change simultaneously (mv=2), breadth-first search depth (ms=2), search only on roads in the same direction (sh=0).'}]}
widthAgent: Reduce the width of Road No.3 by 0.3 meters.
{'command': 'width id=3 v=-0.3'}
widthAgent: Reduce the width of Road No.4 by 0.3 meters.
{'command': 'width id=4 v=-0.3'}
widthAgent: Reduce the width of Road No.5 by 0.3 meters.
{'command': 'width id=5 v=-0.3'}
slopeAgent: Raise Roads 3, 4, and 5 as a whole, change the mode to add, raise by 0.3 meters, both ends change simultaneously (mv=2), breadth-first search depth (ms=2), search only on roads in the same direction (sh=0).
{'command': 'slope id=3 v=0.3 m=add mv=2 ms=2 sh=0'}
commandsList: [{'command': 'width id=3 v=-0.3'}, {'command': 'width id=4 v=-0.3'}, {'command': 'width id=5 v=-0.3'}, {'command': 'slope id=3 v=0.3 m=add mv=2 ms=2 sh=0'}]
Single_Agent:
{'commands': [{'command': 'width id=3 v=-0.3'}, {'command': 'width id=4 v=-0.3'}, {'command': 'width id=5 v=-0.3'}, {'command': 'slope id=3 v=1 m=add mv=2'}, {'command': 'slope id=4 v=1 m=add mv=2'}, {'command': 'slope id=5 v=1 m=add mv=2'}]}
=================================================================
Question:Increase the slope of Road 6 and the two subsequent roads in the same direction,
Multi-agent:
{'commands': [{'type_name': 'slope', 'description': 'Increase the slope of Road 6 by 1 degree, change the mode to angle, change the road end (mv=1), breadth-first search layer (ms=2), search only on roads in the same direction (sh=1)'}]}
slopeAgent: Increase the slope of Road 6 by 1 degree, change the mode to angle, change the road end (mv=1), breadth-first search layer (ms=2), search only on roads in the same direction (sh=1)
{'command': 'slope id=6 v=1 m=angle mv=1 ms=2 sh=1'}
commandsList: [{'command': 'slope id=6 v=1 m=angle mv=1 ms=2 sh=1'}]
Single_Agent:
{'commands': [{'command': 'slope id=6 v=1 m=angle mv=1 ms=2 sh=1'}]}
=================================================================
Question:Narrow Roads 3 to 5 by 0.3 meters each, and additionally raise the slope of Road 4 slightly more than the others,
Multi-agent:
{'commands': [{'type_name': 'width', 'description': 'Reduce the width of Road No.3 by 0.3 meters.'}, {'type_name': 'width', 'description': 'Reduce the width of Road No.4 by 0.3 meters.'}, {'type_name': 'width', 'description': 'Reduce the width of Road No.5 by 0.3 meters.'}, {'type_name': 'slope', 'description': 'Raise the slope of Road 4 by 1 degree, change the mode to angle.'}, {'type_name': 'slope', 'description': 'Raise the slope of Road 3 by 0.5 degrees, change the mode to angle.'}, {'type_name': 'slope', 'description': 'Raise the slope of Road 5 by 0.5 degrees, change the mode to angle.'}]}
widthAgent: Reduce the width of Road No.3 by 0.3 meters.
{'command': 'width id=3 v=-0.3'}
widthAgent: Reduce the width of Road No.4 by 0.3 meters.
{'command': 'width id=4 v=-0.3'}
widthAgent: Reduce the width of Road No.5 by 0.3 meters.
{'command': 'width id=5 v=-0.3'}
slopeAgent: Raise the slope of Road 4 by 1 degree, change the mode to angle.
{'command': 'slope id=4 v=1 m=angle'}
slopeAgent: Raise the slope of Road 3 by 0.5 degrees, change the mode to angle.
{'command': 'slope id=3 v=0.5 m=angle'}
slopeAgent: Raise the slope of Road 5 by 0.5 degrees, change the mode to angle.
{'command': 'slope id=5 v=0.5 m=angle'}
commandsList: [{'command': 'width id=3 v=-0.3'}, {'command': 'width id=4 v=-0.3'}, {'command': 'width id=5 v=-0.3'}, {'command': 'slope id=4 v=1 m=angle'}, {'command': 'slope id=3 v=0.5 m=angle'}, {'command': 'slope id=5 v=0.5 m=angle'}]
Single_Agent:
{'commands': [{'command': 'width id=3 v=-0.3'}, {'command': 'width id=4 v=-0.3'}, {'command': 'width id=5 v=-0.3'}, {'command': 'slope id=3 v=2 m=angle'}, {'command': 'slope id=4 v=3 m=angle'}, {'command': 'slope id=5 v=2 m=angle'}]}
=================================================================
Question:Increase the initial curvature of Road 15 and narrow Road 3 by 0.3m. Additionally, raise the end of Road 4 by 3m, and apply the same treatment to Road 5 as to Road 4.
Multi-agent:
{'commands': [{'type_name': 'curve', 'description': 'Increase the parameter v0 of road 15 by 0.2 (increase the initial curvature).'}, {'type_name': 'width', 'description': 'Reduce the width of Road No.3 by 0.3 m.'}, {'type_name': 'slope', 'description': 'Raise the end of Road 4 by 3 m, modify using add mode, mv=1 (road end).'}, {'type_name': 'slope', 'description': 'Raise the end of Road 5 by 3 m, modify using add mode, mv=1 (road end).'}]}
curveAgent: Increase the parameter v0 of road 15 by 0.2 (increase the initial curvature).
{'command': 'curve id=15 v0=0.2'}
widthAgent: Reduce the width of Road No.3 by 0.3 m.
{'command': 'width id=3 v=-0.3'}
slopeAgent: Raise the end of Road 4 by 3 m, modify using add mode, mv=1 (road end).
{'command': 'slope id=4 v=3 mv=1 m=add'}
slopeAgent: Raise the end of Road 5 by 3 m, modify using add mode, mv=1 (road end).
{'command': 'slope id=5 v=3 mv=1 m=add'}
commandsList: [{'command': 'curve id=15 v0=0.2'}, {'command': 'width id=3 v=-0.3'}, {'command': 'slope id=4 v=3 mv=1 m=add'}, {'command': 'slope id=5 v=3 mv=1 m=add'}]
Single_Agent:
{'commands': [{'command': 'curve id=15 h0=10 v0=0.2'}, {'command': 'width id=3 v=-0.3'}, {'command': 'slope id=4 v=3 m=add mv=1'}, {'command': 'slope id=5 v=3 m=add mv=1'}]}
=================================================================








Current Model:gemini
Question:The starting point of Road 1 is too high, lower it by 2 meters.
Multi-agent:
{'commands': [{'type_name': 'slope', 'description': 'Lower the starting point of Road 1 by 2 meters, modify using add mode, mv=0 (road start).'}]}
slopeAgent: Lower the starting point of Road 1 by 2 meters, modify using add mode, mv=0 (road start).
{'command': 'slope id=1 v=-2 m=add mv=0'}
commandsList: [{'command': 'slope id=1 v=-2 m=add mv=0'}]
Single_Agent:
{'commands': [{'command': 'slope id=1 v=-2 m=add mv=0'}]}
=================================================================
Question:Widen Road 6 by 0.5 meters and increase its slope slightly.
Multi-agent:
{'commands': [{'type_name': 'width', 'description': 'Increase the width of Road 6 by 0.5 meters.'}, {'type_name': 'slope', 'description': 'Increase the slope of Road 6 by 5 degrees, change the mode to angle, and change the position to the end of the road (mv=1).'}]}
widthAgent: Increase the width of Road 6 by 0.5 meters.
{'command': 'width id=6 v=0.5'}
slopeAgent: Increase the slope of Road 6 by 5 degrees, change the mode to angle, and change the position to the end of the road (mv=1).
{'command': 'slope id=6 v=5 m=angle mv=1'}
commandsList: [{'command': 'width id=6 v=0.5'}, {'command': 'slope id=6 v=5 m=angle mv=1'}]
Single_Agent:
{'commands': [{'command': 'width id=6 v=0.5'}, {'command': 'slope id=6 v=5 m=angle mv=2'}]}
=================================================================
Question:Narrow the width of both Road 5 and Road 6, and smooth out the connection between them.
Multi-agent:
{'commands': [{'type_name': 'width', 'description': 'Reduce the width of Road 5 by 0.5 meters, and smooth the edge connecting to other roads (s=1).'}, {'type_name': 'width', 'description': 'Reduce the width of Road 6 by 0.5 meters, and smooth the edge connecting to other roads (s=1).'}]}
widthAgent: Reduce the width of Road 5 by 0.5 meters, and smooth the edge connecting to other roads (s=1).
{'command': 'width id=5 v=-0.5 s=1'}
widthAgent: Reduce the width of Road 6 by 0.5 meters, and smooth the edge connecting to other roads (s=1).
{'command': 'width id=6 v=-0.5 s=1'}
commandsList: [{'command': 'width id=5 v=-0.5 s=1'}, {'command': 'width id=6 v=-0.5 s=1'}]
Single_Agent:
{'commands': [{'command': 'width id=5 v=-0.2 s=1'}, {'command': 'width id=6 v=-0.2 s=1'}]}
=================================================================
Question:Narrow the width of Roads 3 to 5 by 0.3 meters. Then raise the elevation of these three roads as a whole,
Multi-agent:
{'commands': [{'type_name': 'width', 'description': 'Reduce the width of Road No. 3 by 0.3 meters.'}, {'type_name': 'width', 'description': 'Reduce the width of Road No. 4 by 0.3 meters.'}, {'type_name': 'width', 'description': 'Reduce the width of Road No. 5 by 0.3 meters.'}, {'type_name': 'slope', 'description': 'Raise Road No. 3 as a whole by 1 meter, change the mode to add, and change both ends simultaneously (mv=2).'}, {'type_name': 'slope', 'description': 'Raise Road No. 4 as a whole by 1 meter, change the mode to add, and change both ends simultaneously (mv=2).'}, {'type_name': 'slope', 'description': 'Raise Road No. 5 as a whole by 1 meter, change the mode to add, and change both ends simultaneously (mv=2).'}]}
widthAgent: Reduce the width of Road No. 3 by 0.3 meters.
{'command': 'width id=3 v=-0.3'}
widthAgent: Reduce the width of Road No. 4 by 0.3 meters.
{'command': 'width id=4 v=-0.3'}
widthAgent: Reduce the width of Road No. 5 by 0.3 meters.
{'command': 'width id=5 v=-0.3'}
slopeAgent: Raise Road No. 3 as a whole by 1 meter, change the mode to add, and change both ends simultaneously (mv=2).
{'command': 'slope id=3 v=1 m=add mv=2'}
slopeAgent: Raise Road No. 4 as a whole by 1 meter, change the mode to add, and change both ends simultaneously (mv=2).
{'command': 'slope id=4 v=1 m=add mv=2'}
slopeAgent: Raise Road No. 5 as a whole by 1 meter, change the mode to add, and change both ends simultaneously (mv=2).
{'command': 'slope id=5 v=1 m=add mv=2'}
commandsList: [{'command': 'width id=3 v=-0.3'}, {'command': 'width id=4 v=-0.3'}, {'command': 'width id=5 v=-0.3'}, {'command': 'slope id=3 v=1 m=add mv=2'}, {'command': 'slope id=4 v=1 m=add mv=2'}, {'command': 'slope id=5 v=1 m=add mv=2'}]
Single_Agent:
{'commands': [{'command': 'width id=3 v=-0.3'}, {'command': 'width id=4 v=-0.3'}, {'command': 'width id=5 v=-0.3'}, {'command': 'slope id=3 v=1 m=add mv=2'}, {'command': 'slope id=4 v=1 m=add mv=2'}, {'command': 'slope id=5 v=1 m=add mv=2'}]}
=================================================================
Question:Increase the slope of Road 6 and the two subsequent roads in the same direction,
Multi-agent:
{'commands': [{'type_name': 'slope', 'description': 'Increase the slope of Road 6 by 5 degrees, change the mode to angle, change the position to the end of the road (mv=1), breadth-first search layer (ms=2), search only on roads in the same direction (sh=1)'}]}
slopeAgent: Increase the slope of Road 6 by 5 degrees, change the mode to angle, change the position to the end of the road (mv=1), breadth-first search layer (ms=2), search only on roads in the same direction (sh=1)
{'command': 'slope id=6 v=5 m=angle mv=1 ms=2 sh=1'}
commandsList: [{'command': 'slope id=6 v=5 m=angle mv=1 ms=2 sh=1'}]
Single_Agent:
{'commands': [{'command': 'slope id=6 v=5 m=angle mv=1 ms=2 sh=1'}]}
=================================================================
Question:Narrow Roads 3 to 5 by 0.3 meters each, and additionally raise the slope of Road 4 slightly more than the others,
Multi-agent:
{'commands': [{'type_name': 'width', 'description': 'Reduce the width of Road 3 by 0.3 meters.'}, {'type_name': 'width', 'description': 'Reduce the width of Road 4 by 0.3 meters.'}, {'type_name': 'width', 'description': 'Reduce the width of Road 5 by 0.3 meters.'}, {'type_name': 'slope', 'description': 'Raise the slope of Road 3 by 5 degrees, change the mode to angle, and change the position to the end of the road (mv=1).'}, {'type_name': 'slope', 'description': 'Raise the slope of Road 4 by 7 degrees, change the mode to angle, and change the position to the end of the road (mv=1).'}, {'type_name': 'slope', 'description': 'Raise the slope of Road 5 by 5 degrees, change the mode to angle, and change the position to the end of the road (mv=1).'}]}
widthAgent: Reduce the width of Road 3 by 0.3 meters.
{'command': 'width id=3 v=-0.3'}
widthAgent: Reduce the width of Road 4 by 0.3 meters.
{'command': 'width id=4 v=-0.3'}
widthAgent: Reduce the width of Road 5 by 0.3 meters.
{'command': 'width id=5 v=-0.3'}
slopeAgent: Raise the slope of Road 3 by 5 degrees, change the mode to angle, and change the position to the end of the road (mv=1).
{'command': 'slope id=3 v=5 m=angle mv=1'}
slopeAgent: Raise the slope of Road 4 by 7 degrees, change the mode to angle, and change the position to the end of the road (mv=1).
{'command': 'slope id=4 v=7 m=angle mv=1'}
slopeAgent: Raise the slope of Road 5 by 5 degrees, change the mode to angle, and change the position to the end of the road (mv=1).
{'command': 'slope id=5 v=5 m=angle mv=1'}
commandsList: [{'command': 'width id=3 v=-0.3'}, {'command': 'width id=4 v=-0.3'}, {'command': 'width id=5 v=-0.3'}, {'command': 'slope id=3 v=5 m=angle mv=1'}, {'command': 'slope id=4 v=7 m=angle mv=1'}, {'command': 'slope id=5 v=5 m=angle mv=1'}]
Single_Agent:
{'commands': [{'command': 'width id=3 v=-0.3'}, {'command': 'width id=4 v=-0.3'}, {'command': 'width id=5 v=-0.3'}, {'command': 'slope id=3 v=5 m=angle mv=1'}, {'command': 'slope id=4 v=7 m=angle mv=1'}, {'command': 'slope id=5 v=5 m=angle mv=1'}]}
=================================================================
Question:Increase the initial curvature of Road 15 and narrow Road 3 by 0.3m. Additionally, raise the end of Road 4 by 3m, and apply the same treatment to Road 5 as to Road 4.
Multi-agent:
{'commands': [{'type_name': 'curve', 'description': 'Increase the parameter v0 of road 15 by 0.2 (increase the initial curvature).'}, {'type_name': 'width', 'description': 'Reduce the width of Road 3 by 0.3m.'}, {'type_name': 'slope', 'description': 'Raise the end of Road 4 by 3m, modify using add mode, mv=1 (road end).'}, {'type_name': 'slope', 'description': 'Raise the end of Road 5 by 3m, modify using add mode, mv=1 (road end).'}]}
curveAgent: Increase the parameter v0 of road 15 by 0.2 (increase the initial curvature).
{'command': 'curve id=15 v0=0.2'}
widthAgent: Reduce the width of Road 3 by 0.3m.
{'command': 'width id=3 v=-0.3'}
slopeAgent: Raise the end of Road 4 by 3m, modify using add mode, mv=1 (road end).
{'command': 'slope id=4 v=3 m=add mv=1'}
slopeAgent: Raise the end of Road 5 by 3m, modify using add mode, mv=1 (road end).
{'command': 'slope id=5 v=3 m=add mv=1'}
commandsList: [{'command': 'curve id=15 v0=0.2'}, {'command': 'width id=3 v=-0.3'}, {'command': 'slope id=4 v=3 m=add mv=1'}, {'command': 'slope id=5 v=3 m=add mv=1'}]
Single_Agent:
{'commands': [{'command': 'curve id=15 v0=0.2'}, {'command': 'width id=3 v=-0.3'}, {'command': 'slope id=4 v=3 m=add mv=1'}, {'command': 'slope id=5 v=3 m=add mv=1'}]}
=================================================================
"""




