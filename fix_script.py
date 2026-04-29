import sys

filepath = 'src/scripts/index_knowledge_base.py'
with open(filepath, 'r') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if 'query_timeout=None' in line:
        continue
    if 'IndexingParametersConfiguration(' in line:
        new_lines.append(line)
        new_lines.append('                query_timeout=None,\n')
    else:
        new_lines.append(line)

with open(filepath, 'w') as f:
    f.writelines(new_lines)

print("Script updated successfully.")
