#!/bin/bash
#SBATCH --job-name=my_test_job        # Job name
# SBATCH --chdir=workDirname_1         # Working directory
#SBATCH --output=output.txt           # Standard output file
#SBATCH --error=error.txt             # Standard error file
#SBATCH --partition=general           # Partition or queue name
#SBATCH --qos=general                 # QOS level
#SBATCH --nodes=1                     # Number of nodes
#SBATCH --ntasks-per-node=1           # Number of tasks per node
#SBATCH --cpus-per-task=1             # Number of CPU cores per task
#SBATCH --time=0-00:01:00             # Maximum runtime (D-HH:MM:SS)

#Load necessary modules (if needed)
#module load module_name

#Your job commands go here
#For example:
#python my_script.py

#Optionally, you can include cleanup commands here (e.g., after the job finishes)
#For example:
#rm some_temp_file.txt
echo "Hello, world! to stdout"
echo "Hello, world! to file" > hello.txt
