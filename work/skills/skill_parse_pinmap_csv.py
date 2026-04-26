import csv
import json
import sys

def parse_pinmap(csv_path):
    """
    Skill: Convert KiCad/Altium pin CSV (Netlist) to a canonical JSON dictionary.
    Ex: {"PA8": "TIM1_CH1", "PB0": "BOOT0"}
    """
    pin_mapping = {}
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Need customized rules per EDA tool (Altium vs KiCad)
                pin = row.get("Pin")
                net = row.get("Net")
                if pin and net:
                    pin_mapping[pin] = net
        return pin_mapping
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(json.dumps(parse_pinmap(sys.argv[1]), indent=2))
    else:
        print("Usage: python skill_parse_pinmap_csv.py <path_to_csv>")
