import json
import sys
from datetime import datetime


def merge_jsons(json_list):
    earliest_json = min(json_list, key=lambda x: datetime.fromisoformat(x["start-time"]))
    merged_json = earliest_json.copy()
    merged_json["log-files"] = []
    merged_json["lag"] = {}

    for json_data in json_list:
        merged_json["log-files"].extend(json_data["log-files"])

    for idx, json_data in enumerate(json_list):
        for key, value in json_data["lag"].items():
            if key not in merged_json["lag"]:
                merged_json["lag"][key] = {}
            for inner_key, inner_value in value.items():
                merged_json["lag"][key][str(idx)] = inner_value

    return merged_json


def split_json(input_json):
    data = json.loads(input_json)
    common_fields = {key: data[key] for key in data if key not in ['log-files', 'lag']}
    json_outputs = []

    for log_file in data['log-files']:
        json_obj = common_fields.copy()
        json_obj['log-files'] = [log_file]
        json_obj['lag'] = {}
        json_outputs.append(json_obj)

    for lag_id, lag_info in data['lag'].items():
        file_index = int(list(lag_info.keys())[0])
        json_outputs[file_index]['lag'][lag_id] = {"0": lag_info[str(file_index)]}

    return json_outputs


def process_file(file_path):
    with open(file_path, 'r') as file:
        input_json = file.read()
    return split_json(input_json)


def main():
    json_processed_list = []
    for file_path in sys.argv[1:]:
        splitted_jsons = process_file(file_path)
        for json_obj in splitted_jsons:
            json_processed_list.append(json_obj)
    merged_result = merge_jsons(json_processed_list)

    # Output the merged JSON to a file or print it
    with open('merged_result.json', 'w') as outfile:
        json.dump(merged_result, outfile, indent=4)


if __name__ == "__main__":
    main()

