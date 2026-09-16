#!/bin/bash
#SBATCH --job-name=cfx_astra_3a9fcc9a572e45bfb6a6fcb6_endpoint_bulk_v1
#SBATCH -N 1
#SBATCH -n 64
#SBATCH --partition=amd_256
#SBATCH --time=00:45:00
#SBATCH --chdir=/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_3a9fcc9a572e45bfb6a6fcb6_endpoint_bulk_v1/release
#SBATCH --output=/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_3a9fcc9a572e45bfb6a6fcb6_endpoint_bulk_v1/slurm-%j.out
#SBATCH --error=/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_3a9fcc9a572e45bfb6a6fcb6_endpoint_bulk_v1/slurm-%j.err
#SBATCH --signal=B:TERM@30
set -euo pipefail
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
unset PYTHONPATH LD_PRELOAD LD_LIBRARY_PATH
export CFX_SCIENCE_LOG_DIR=/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_3a9fcc9a572e45bfb6a6fcb6_endpoint_bulk_v1/runtime_logs
export PYTHONPATH=/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_3a9fcc9a572e45bfb6a6fcb6_endpoint_bulk_v1/release/kinetic_agents/evaluation/science_startup:/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_3a9fcc9a572e45bfb6a6fcb6_endpoint_bulk_v1/release
exec /public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/pilot-env-20260912-b/venv/bin/python -B -m kinetic_agents.evaluation.service execute --root /public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_3a9fcc9a572e45bfb6a6fcb6_endpoint_bulk_v1 --contract-sha e1df00d1020caf433e98728098ebc7b976e2be98f4b88f82f41aa76ea3b00153
