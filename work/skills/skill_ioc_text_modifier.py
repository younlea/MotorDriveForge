import re
import sys

def modify_ioc(ioc_template_path, config_dict, output_path):
    """
    Skill: Takes a base .ioc file and securely modifies its content based on the config_dict.
    config_dict = {"PA8.Signal": "TIM1_CH1", "PA8.Locked": "true"}
    """
    with open(ioc_template_path, 'r') as f:
        content = f.read()

    for key, val in config_dict.items():
        # regex to replace existing key or append it
        pattern = rf"^{re.escape(key)}=.*$"
        if re.search(pattern, content, re.MULTILINE):
            content = re.sub(pattern, f"{key}={val}", content, flags=re.MULTILINE)
        else:
            content += f"\n{key}={val}"

    with open(output_path, 'w') as f:
        f.write(content)
    return True

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python skill_ioc_text_modifier.py template.ioc output.ioc")
    else:
        modify_ioc(sys.argv[1], {"TEST.Test": "123"}, sys.argv[2])
