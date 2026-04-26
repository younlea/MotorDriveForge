import sys

def inject_c_snippet(target_c_file, snippet_text):
    """
    Skill: Safely inject C string into the /* USER CODE BEGIN 2 */ tag.
    """
    with open(target_c_file, 'r') as f:
        lines = f.readlines()
        
    injected_lines = []
    for line in lines:
        injected_lines.append(line)
        if "/* USER CODE BEGIN 2 */" in line:
            injected_lines.append(f"  {snippet_text}\n")
            
    with open(target_c_file, 'w') as f:
        f.writelines(injected_lines)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        pass
    else:
        inject_c_snippet(sys.argv[1], sys.argv[2])
