

# this RLLIB could also be interesting
# https://docs.ray.io/en/latest/rllib/index.html
# this requires python 3.9, currently installed 3.11 on laptop

import gymnasium as gym

from stable_baselines3 import PPO
from stable_baselines3.common.env_util import make_vec_env

from stable_baselines3.common.utils import set_random_seed


from stable_baselines3.common.vec_env import DummyVecEnv

import gymEnv.carsimGymEnv as carsimGymEnv

from myPPO.myPPO import myPPO

from gymEnv.myEnums import MapType
from gymEnv.myEnums import LightSetting
from gymEnv.myEnums import Spawn

import torch
from torch.utils.tensorboard import SummaryWriter


import hydra
from hydra.core.hydra_config import HydraConfig
from omegaconf import OmegaConf


import logging
import os
import random

def run_ppo(cfg):
    logger = SummaryWriter(log_dir="./tmp")
    log_dir = "tmp/"
    os.makedirs(log_dir, exist_ok=True)

    logger.add_text("Configs", repr(cfg))

    seed = cfg.seed
    
    print(f"Torch Seed before {torch.get_rng_state()}")

    set_random_seed(seed)
    # https://stable-baselines3.readthedocs.io/en/master/guide/algos.html#reproducibility
    logger.add_text("Torch Seed", f"{torch.get_rng_state()}")

    print(f'a random torch int {torch.randint(0, 100, (1,))}')
    print(f'a random torch int2 {torch.randint(0, 100, (1,))}')

    print(f'a random int {random.randint(0, 100)}')
    print(f'a random int2 {random.randint(0, 100)}')
    
    print(f"Torch Seed after {torch.get_rng_state()}")

    # we use own replay buffer that saves the observation space as uint8 instead of float32
    # int8 is 8bit, float32 is 32bit

    print(f"Working directory : {os.getcwd()}")
    print(f"Output directory  : {hydra.core.hydra_config.HydraConfig.get().runtime.output_dir}")

   
    # TODO do we need some approaches from RL path/trajectory planning to complete the parcour?


    n_envs = cfg.n_envs

    env_kwargs = OmegaConf.to_container(cfg.env_kwargs)
    env_kwargs["trainingMapType"] = MapType[cfg.env_kwargs.trainingMapType]
    env_kwargs["trainingLightSetting"] = LightSetting[cfg.env_kwargs.trainingLightSetting]
    # get proper enum type from string
    env_kwargs["spawn_point"] = Spawn[cfg.env_kwargs.spawn_point]



    # Parallel environments
    vec_env = make_vec_env(carsimGymEnv.BaseCarsimEnv, n_envs=n_envs, env_kwargs=env_kwargs)
    # the n_envs can quickly be too much since the replay buffer will grow
    # the observations are quite big (float32)

    
    vec_env.env_method(
        method_name="setSeedUnity",
        indices=0,
        seed = seed,
    )


    # set one log to true
    # ---> some logging of the stacked frames to see what the memory is like
    # there is some image printing to the imagelog folder
    vec_env.env_method(
        method_name="setLog",
        indices=0,
        log=False,
    )

    algo = myPPO

    policy_kwargs = {"normalize_images": cfg.env_kwargs.image_preprocessing.normalize_images, "net_arch": OmegaConf.to_container(cfg.algo_settings.net_arch)}

    # normalize_imagess=True scales the images to 0-1 range
    # requires dtype float32
    # kwarg to both the env (ObsSpace) and the policy


    model = algo(cfg.algo_settings.policy, vec_env, verbose=1,
                tensorboard_log="./tmp", n_epochs=cfg.algo_settings.n_epochs, batch_size=cfg.algo_settings.batch_size, n_steps=cfg.algo_settings.n_steps, policy_kwargs=policy_kwargs, seed = seed, use_bundled_calls=cfg.algo_settings.use_bundled_calls, use_fresh_obs=cfg.algo_settings.use_fresh_obs, print_network_and_loss_structure=cfg.algo_settings.print_network_and_loss_structure)
    # CnnPolicy network architecture can be seen in sb3.common.torch_layers.py

    print(f"model weights for seed verification: {model.policy.value_net.weight[0][0:5]}")
    # [-0.0591, -0.0703,  0.0513, -0.1466,  0.0055]

    # TODO wo ist epsilon definiert?
    # nimmt epsilon mit der Zeit ab?
    # es gibt einen ent_coef was exploration fördert

    # TODO preprocessing steps help?
    # increase contrast of images?
    # https://stackoverflow.com/questions/39308030/how-do-i-increase-the-contrast-of-an-image-in-python-opencv



    
    if cfg.copy_model_from:
        string = f"{HydraConfig.get().runtime.cwd}/{cfg.copy_model_from}"
        print(f'loading model from {string} before learning')
        model = algo.load(string, env=vec_env, tensorboard_log="./tmp",
                        n_epochs=cfg.algo_settings.n_epochs, batch_size=cfg.algo_settings.batch_size)

    if not cfg.eval_settings.eval_only:
        model.learn(total_timesteps=cfg.total_timesteps, log_interval=cfg.eval_settings.interval_during_learn, n_eval_episodes = cfg.eval_settings.n_eval_episodes, eval_light_settings=cfg.eval_settings.eval_light_settings)
        model.save("finished_ppo")
        print("finished learning without issues")

    
    # load best model and eval it again
    if not cfg.eval_settings.eval_only:
        best_model_name = model.rollout_best_model_name
        print(f'loading best model {best_model_name} after learning')
        model.load(best_model_name)
        model.use_bundled_calls = cfg.algo_settings.use_bundled_calls
        model.use_fresh_obs=cfg.algo_settings.use_fresh_obs

    if not cfg.episode_record_replay_settings.replay_folder:
        model.record_episodes(cfg.episode_record_replay_settings, seed, cfg)
    else:
        print(f"replaying episodes from {cfg.episode_record_replay_settings.replay_folder}")
    model.replay_episodes(cfg.episode_record_replay_settings, seed, cfg.env_kwargs.fixedTimestepsLength)
    
    # run more evals here after training completed or when eval only
    model.eval_only(total_eval_runs=cfg.eval_settings.number_eval_runs, n_eval_episodes = cfg.eval_settings.n_eval_episodes, eval_light_settings=cfg.eval_settings.eval_light_settings, offset=model.num_timesteps)
    


@hydra.main(config_path=".", config_name="cfg/ppo.yaml")
def main(cfg):

    # run specific config files with:
    # python sb3_ppo.py --config-name cfg/ppo_isolated_medium_standard.yaml

    logging.info(f"cfg {cfg}")

    with open('config.yaml', 'w') as f:
        OmegaConf.save(cfg, f)

    import cProfile
    cProfile.runctx('run_ppo(cfg.cfg)', globals(), locals(), sort='cumtime')
    #run_ppo(cfg.cfg)

if __name__ == "__main__":
    main()