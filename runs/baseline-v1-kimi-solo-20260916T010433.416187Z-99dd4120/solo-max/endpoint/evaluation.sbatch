#!/bin/bash
#SBATCH --job-name=cfx_astra_8975b1c396724bdfaca362a1_endpoint
#SBATCH -N 1
#SBATCH -n 64
#SBATCH --partition=amd_256
#SBATCH --time=00:58:00
#SBATCH --chdir=/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_8975b1c396724bdfaca362a1_endpoint/release
#SBATCH --output=/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_8975b1c396724bdfaca362a1_endpoint/slurm-%j.out
#SBATCH --error=/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_8975b1c396724bdfaca362a1_endpoint/slurm-%j.err
#SBATCH --signal=B:TERM@30
set -euo pipefail
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
unset PYTHONPATH LD_PRELOAD LD_LIBRARY_PATH
export CFX_SCIENCE_LOG_DIR=/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_8975b1c396724bdfaca362a1_endpoint/runtime_logs
export PYTHONPATH=/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_8975b1c396724bdfaca362a1_endpoint/release/kinetic_agents/evaluation/science_startup:/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_8975b1c396724bdfaca362a1_endpoint/release
exec /public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/pilot-env-20260912-b/venv/bin/python -B -m kinetic_agents.evaluation.service execute --root /public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_8975b1c396724bdfaca362a1_endpoint --contract-sha 2fa6309f673de06d90cdb23d6e7f9d7f43cd9855701209026b5b2c668ed9cb39
