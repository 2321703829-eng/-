import random
import time

from  td import *

SAMPLE_SIZE = 230
REDUCED_OPTION_WEIGHT = 0.25


def get_random_indices(sample_size):
    if not 0 <= sample_size <= len(jsons_all):
        raise ValueError(f"sample_size must be between 0 and {len(jsons_all)}")

    available_indices = list(range(len(jsons_all)))
    selected_indices = []

    while len(selected_indices) < sample_size:
        weights = [
            REDUCED_OPTION_WEIGHT
            if xodrs_all[index].startswith("DEU_AAH") or "Location" in xodrs_all[index]
            else 1.0
            for index in available_indices
        ]
        selected_position = random.choices(
            range(len(available_indices)), weights=weights, k=1
        )[0]
        selected_indices.append(available_indices.pop(selected_position))

    return selected_indices


random_list = get_random_indices(SAMPLE_SIZE)
random_list.sort()
# print(random_list)
time_ = time.strftime("%m-%d")
for i in random_list:
    xodr_name = xodrs_all[i]
    json_name = jsons_all[i][:-5]
    print(f"openxodr {xodr_name}")
    print(f"openjson {json_name}")
    print(f"xodrsavename {json_name}_{time_}_random_ws")
    print(f"jsonsavename {json_name}_{time_}_random_ws")
    print("random")
print("close")

# for i in random_list:
#     print(f"{jsons_all[i]}")

statistics = {
    "DEU_AAH1": 0,
    "DEU_AAH2": 0,
    "DEU_AAH3": 0,
    "DEU_AAH4": 0,
    "Location": 0,
    "Ffb-2_1": 0,
    "Zip-1_14": 0,
}
other_count = 0

for index in random_list:
    xodr_name = xodrs_all[index]
    for statistic_name in statistics:
        if statistic_name in xodr_name:
            statistics[statistic_name] += 1
            break
    else:
        other_count += 1

statistics["Other"] = other_count

total_count = len(random_list)
aah_count = sum(
    count for name, count in statistics.items() if name.startswith("DEU_AAH")
)
reduced_count = aah_count + statistics["Location"]

print("\nSelection statistics:")
print(f"Total: {total_count}")
for name, count in statistics.items():
    print(f"{name}: {count} ({count / total_count:.1%})")
print(f"Reduced options: {reduced_count} ({reduced_count / total_count:.1%})")


# for i in range(len(temJson)):
#     print(f"openxodr {temXodr[i]}")
#     print(f"openjson {temJson[i]}")
#     # print(f"savename {temXodr[i]}")
#     # print(f"jsonsavename {temJson[i]}")
#     print("tongji")
