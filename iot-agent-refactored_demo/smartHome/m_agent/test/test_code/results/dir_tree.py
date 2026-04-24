import os

def tree(dir_path, prefix=""):
    files = os.listdir(dir_path)
    for i, file in enumerate(files):
        path = os.path.join(dir_path, file)
        is_last = (i == len(files) - 1)

        if is_last:
            print(prefix + "└── " + file)
            new_prefix = prefix + "    "
        else:
            print(prefix + "├── " + file)
            new_prefix = prefix + "│   "

        if os.path.isdir(path):
            tree(path, new_prefix)

tree(".")
