#!/bin/bash
#SBATCH --job-name=cfx_astra_f95ef3f81ca44de7ab8c09cb_endpoint
#SBATCH -N 1
#SBATCH -n 64
#SBATCH --partition=amd_256
#SBATCH --time=00:58:00
#SBATCH --chdir=/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_f95ef3f81ca44de7ab8c09cb_endpoint/release
#SBATCH --output=/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_f95ef3f81ca44de7ab8c09cb_endpoint/slurm-%j.out
#SBATCH --error=/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_f95ef3f81ca44de7ab8c09cb_endpoint/slurm-%j.err
#SBATCH --signal=B:TERM@30
set -euo pipefail
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
unset PYTHONPATH LD_PRELOAD LD_LIBRARY_PATH
export CFX_SCIENCE_LOG_DIR=/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_f95ef3f81ca44de7ab8c09cb_endpoint/runtime_logs
export PYTHONPATH=/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_f95ef3f81ca44de7ab8c09cb_endpoint/release/kinetic_agents/evaluation/science_startup:/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_f95ef3f81ca44de7ab8c09cb_endpoint/release
exec /public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/pilot-env-20260912-b/venv/bin/python -B -m kinetic_agents.evaluation.service execute --root /public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_f95ef3f81ca44de7ab8c09cb_endpoint --contract-sha 5a2b96da2e55c086d7418227f0422d7a6274d67181847e2289d5cdbab598a1f9
