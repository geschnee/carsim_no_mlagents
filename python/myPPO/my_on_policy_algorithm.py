import sys
import time
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar, Union

import numpy as np
import torch as th
from gymnasium import spaces

from stable_baselines3.common.base_class import BaseAlgorithm
from stable_baselines3.common.buffers import DictRolloutBuffer, RolloutBuffer
from stable_baselines3.common.callbacks import BaseCallback
from stable_baselines3.common.policies import ActorCriticPolicy
from stable_baselines3.common.type_aliases import GymEnv, MaybeCallback, Schedule
from stable_baselines3.common.utils import obs_as_tensor, safe_mean
from stable_baselines3.common.vec_env import VecEnv

from myPPO.my_buffers import MyRolloutBuffer

from gymEnv.myEnums import LightSetting
from gymEnv.myEnums import MapType
from gymEnv.myEnums import Spawn

from myPPO.game_repr import GameRepresentation
from myPPO.games_results import GamesResults
import collections

import os
import csv

SelfOnPolicyAlgorithm = TypeVar(
    "SelfOnPolicyAlgorithm", bound="MyOnPolicyAlgorithm")

# what this code is based on:
# https://stable-baselines3.readthedocs.io/en/master/_modules/stable_baselines3/common/on_policy_algorithm.html
class MyOnPolicyAlgorithm(BaseAlgorithm):
    """
    The base for On-Policy algorithms (ex: A2C/PPO).

    :param policy: The policy model to use (MlpPolicy, CnnPolicy, ...)
    :param env: The environment to learn from (if registered in Gym, can be str)
    :param learning_rate: The learning rate, it can be a function
        of the current progress remaining (from 1 to 0)
    :param n_steps: The number of steps to run for each environment per update
        (i.e. batch size is n_steps * n_env where n_env is number of environment copies running in parallel)
    :param gamma: Discount factor
    :param gae_lambda: Factor for trade-off of bias vs variance for Generalized Advantage Estimator.
        Equivalent to classic advantage when set to 1.
    :param ent_coef: Entropy coefficient for the loss calculation
    :param vf_coef: Value function coefficient for the loss calculation
    :param max_grad_norm: The maximum value for the gradient clipping
    :param use_sde: Whether to use generalized State Dependent Exploration (gSDE)
        instead of action noise exploration (default: False)
    :param sde_sample_freq: Sample a new noise matrix every n steps when using gSDE
        Default: -1 (only sample at the beginning of the rollout)
    :param stats_window_size: Window size for the rollout logging, specifying the number of episodes to average
        the reported success rate, mean episode length, and mean reward over
    :param tensorboard_log: the log location for tensorboard (if None, no logging)
    :param monitor_wrapper: When creating an environment, whether to wrap it
        or not in a Monitor wrapper.
    :param policy_kwargs: additional arguments to be passed to the policy on creation
    :param verbose: Verbosity level: 0 for no output, 1 for info messages (such as device or wrappers used), 2 for
        debug messages
    :param seed: Seed for the pseudo random generators
    :param device: Device (cpu, cuda, ...) on which the code should be run.
        Setting it to auto, the code will be run on the GPU if possible.
    :param _init_setup_model: Whether or not to build the network at the creation of the instance
    :param supported_action_spaces: The action spaces supported by the algorithm.
    """

    rollout_buffer: MyRolloutBuffer
    policy: ActorCriticPolicy

    def __init__(
        self,
        policy: Union[str, Type[ActorCriticPolicy]],
        env: Union[GymEnv, str],
        learning_rate: Union[float, Schedule],
        n_steps: int,
        gamma: float,
        gae_lambda: float,
        ent_coef: float,
        vf_coef: float,
        max_grad_norm: float,
        use_sde: bool,
        sde_sample_freq: int,
        stats_window_size: int = 100,
        tensorboard_log: Optional[str] = None,
        monitor_wrapper: bool = True,
        policy_kwargs: Optional[Dict[str, Any]] = None,
        verbose: int = 0,
        seed: Optional[int] = None,
        device: Union[th.device, str] = "auto",
        _init_setup_model: bool = True,
        supported_action_spaces: Optional[Tuple[Type[spaces.Space], ...]] = None,
        use_bundled_calls: bool = False,
        use_fresh_obs: bool = False,
        print_network_and_loss_structure: bool = False,
    ):
        super().__init__(
            policy=policy,
            env=env,
            learning_rate=learning_rate,
            policy_kwargs=policy_kwargs,
            verbose=verbose,
            device=device,
            use_sde=use_sde,
            sde_sample_freq=sde_sample_freq,
            support_multi_env=True,
            seed=seed,
            stats_window_size=stats_window_size,
            tensorboard_log=tensorboard_log,
            supported_action_spaces=supported_action_spaces,
        )

        self.n_steps = n_steps
        self.gamma = gamma
        self.gae_lambda = gae_lambda
        self.ent_coef = ent_coef
        self.vf_coef = vf_coef
        self.max_grad_norm = max_grad_norm

        self.use_bundled_calls = use_bundled_calls
        print(f'use_bundled_calls: {use_bundled_calls}')
        
        self.use_fresh_obs = use_fresh_obs
        self.print_network_and_loss_structure = print_network_and_loss_structure

        self.my_logs = {}

        if _init_setup_model:
            self._setup_model()

    def _setup_model(self) -> None:
        self._setup_lr_schedule()
        self.set_random_seed(self.seed)

        buffer_cls = DictRolloutBuffer if isinstance(
            self.observation_space, spaces.Dict) else MyRolloutBuffer

        self.rollout_buffer = buffer_cls(
            self.n_steps,
            self.observation_space,
            self.action_space,
            device=self.device,
            gamma=self.gamma,
            gae_lambda=self.gae_lambda,
            n_envs=self.n_envs,
        )
        print(f'policy class: {self.policy_class}')

        # pytype:disable=not-instantiable
        self.policy = self.policy_class(  # type: ignore[assignment]
            self.observation_space, self.action_space, self.lr_schedule, use_sde=self.use_sde, **self.policy_kwargs
        )
        # pytype:enable=not-instantiable
        self.policy = self.policy.to(self.device)

    def inferFromObservations(self, env, deterministic = False):
        # moved to its own function to be able to profile the forward passes (during rollout collection and evaluation) easily


        with th.no_grad():
            # Convert to pytorch tensor or to TensorDict

            #print(f'using fresh obs: {self.use_fresh_obs} use_bundled_calls {self.use_bundled_calls}', flush=True)
            
            if self.use_fresh_obs:

                if self.use_bundled_calls:
                    obs = get_obs_bundled_calls(env)
                else:
                    obs = get_obs_single_calls(env)
            
                #print(f'fresh_obs shape: {fresh_obs.shape}', flush=True)
                obs_tensor = obs_as_tensor(obs, self.device)
            else:

                # we use the obs from the last step, no fresh collection
                # this is how it was originally done in sb3 and uses less calls to unity
                obs_tensor = obs_as_tensor(self._last_obs, self.device)
                obs = self._last_obs

            actions, values, log_probs = self.policy(obs_tensor, deterministic=deterministic)
        
        return actions, values, log_probs, obs

    def print_network_structure(self, env):
        if self.network_printed:
            return
        
        # do not use no_grad here
        # Convert to pytorch tensor or to TensorDict
        fresh_obs = get_obs_single_calls(env)
        obs_tensor = obs_as_tensor(fresh_obs, self.device)

        actions, values, log_probs = self.policy(obs_tensor)

        # https://stackoverflow.com/questions/52468956/how-do-i-visualize-a-net-in-pytorch
        from torchviz import make_dot
        make_dot(actions).render(
            "action_graph", format="png")
        
        print("network printed", flush= True)

        # die graphen sind in den working directories (outputs/.../... zu finden)

        self.network_printed = True

    def collect_rollouts(
        self,
        env: VecEnv,
        callback: BaseCallback,
        rollout_buffer: MyRolloutBuffer,
        n_rollout_steps: int,
    ) -> bool:
        """
        Collect experiences using the current policy and fill a ``RolloutBuffer``.
        The term rollout here refers to the model-free notion and should not
        be used with the concept of rollout used in model-based RL or planning.

        :param env: The training environment
        :param callback: Callback that will be called at each step
            (and at the beginning and end of the rollout)
        :param rollout_buffer: Buffer to fill with rollouts
        :param n_rollout_steps: Number of experiences to collect per environment
        :return: True if function returned with at least `n_rollout_steps`
            collected, False if callback terminated rollout prematurely.
        """

        print(f'collect rollouts started', flush=True)
        cr_time = time.time() 

        # Switch to eval mode (this affects batch norm / dropout)
        self.policy.set_training_mode(False)

        n_steps = 0
        rollout_buffer.reset()
        # Sample new weights for the state dependent exploration
        if self.use_sde:
            self.policy.reset_noise(env.num_envs)

        callback.on_rollout_start()

        
        games_results = GamesResults()

        total_timesteps = 0
        

        reward_correction_dict = {}
        for i in range(env.num_envs):
            reward_correction_dict[i] = {}
        # outer dictionary maps env index to inner dictionary
        # inner dictionary maps step number to the corresponding position in rollout_buffer

        
        new_obs = env.reset()
        # we need to reset the env to get the correct rewards

        if not self.use_fresh_obs:
            self._last_obs = new_obs

        while n_steps < n_rollout_steps:
            if self.use_sde and self.sde_sample_freq > 0 and n_steps % self.sde_sample_freq == 0:
                # Sample a new noise matrix
                self.policy.reset_noise(env.num_envs)

            

            actions, values, log_probs, obs = self.inferFromObservations(env)

            actions = actions.cpu().numpy()

            # Rescale and perform action
            clipped_actions = actions
            # Clip the actions to avoid out of bound error
            if isinstance(self.action_space, spaces.Box):
                clipped_actions = np.clip(
                    actions, self.action_space.low, self.action_space.high)

            new_obs, rewards, dones, infos = step_wrapper(env, clipped_actions, self.use_bundled_calls)
            #print(f'observations shape: {new_obs.shape} {type(new_obs)}')
            #print(f'rewards shape: {rewards.shape} {type(rewards)}')
            #print(f'dones shape: {dones.shape} {type(dones)}')
            #print(f'infos shape: {len(infos)} {type(infos)}')
            # observations shape: (10, 30, 84, 250) <class 'numpy.ndarray'>
            # rewards shape: (10,) <class 'numpy.ndarray'>
            # dones shape: (10,) <class 'numpy.ndarray'>
            # infos shape: 10 <class 'list'>

            self.num_timesteps += env.num_envs
            total_timesteps += env.num_envs

            # Give access to local variables
            callback.update_locals(locals())
            if callback.on_step() is False:
                return False

            self._update_info_buffer(infos)
            n_steps += 1

            if isinstance(self.action_space, spaces.Discrete):
                # Reshape in case of discrete action
                actions = actions.reshape(-1, 1)

            # Handle timeout by bootstraping with value function
            # see GitHub issue #633
            for idx, done in enumerate(dones):
                if (
                    done
                    and infos[idx].get("terminal_observation") is not None
                    and infos[idx].get("TimeLimit.truncated", False)
                ):
                    terminal_obs = self.policy.obs_to_tensor(
                        infos[idx]["terminal_observation"])[0]
                    with th.no_grad():
                        terminal_value = self.policy.predict_values(
                            terminal_obs)[0]  # type: ignore[arg-type]
                    rewards[idx] += self.gamma * terminal_value

                if done:
                    games_results.processInfoDictEpisodeFinished(infos[idx])
                    
                    

            insertpos = rollout_buffer.add(
                obs,  # type: ignore[arg-type]
                actions,
                rewards,
                self._last_episode_starts,  # type: ignore[arg-type]
                values,
                log_probs,
            )
            #print(f'insertpos: {insertpos}')

            assert len(infos) == env.num_envs, f"infos has wrong length {len(infos)} != {env.num_envs}"

            for idx, info in enumerate(infos):

                assert int(info['step']) not in reward_correction_dict[idx].keys(), f"step {info['step']} already in reward correction dict for env {idx}"
                if int(info['step']) != 0:
                    #print(f'info["step"]: {info["step"]}')
                    #print(f'reward_correction_dict[idx]: {reward_correction_dict[idx]}')
                    #print(f'done: {dones[idx]}')
                    assert int(info["step"]) == max(reward_correction_dict[idx].keys()) + 1, f"step {info['step']} is not the next step in reward correction dict for env {idx}: {reward_correction_dict[idx]}"
                
                reward_correction_dict[idx][int(info['step'])] = insertpos

            for idx, done in enumerate(dones):
                # obtain the real rewards from the env
                if done:
                    self.collected_episodes += 1

                if done or n_steps >= n_rollout_steps:
                    # playout finished or cancelling due to enough collected datapoints
                    # TODO it would be better to remove the ones prematurely terminated from the replay buffer

                    env_id = idx
                    rewards = infos[env_id]['rewards']
                    assert len(rewards) == len(reward_correction_dict[env_id]), f"rewards {len(rewards)} and reward correction dict {len(reward_correction_dict[env_id])} do not match in length"
                    for step, bufferpos in reward_correction_dict[env_id].items():
                        rollout_buffer.rewards[bufferpos][env_id] = rewards[step]


                    assert len(reward_correction_dict[env_id]) == int(infos[idx]['amount_of_steps']), f"reward correction dict is not complete {len(reward_correction_dict[env_id])} != {infos[idx]['amount_of_steps']}"

                    # reset the reward correction dict
                    reward_correction_dict[env_id] = {}
                
                if done and self.use_bundled_calls:
                    env.envs[idx].reset()
                    # we need to do the reset here, since our bundled calls do not reset by themselves


            
            self._last_episode_starts = dones
            if not self.use_fresh_obs:
                self._last_obs = new_obs

        with th.no_grad():
            # Compute value for the last timestep
            values = self.policy.predict_values(obs_as_tensor(
                new_obs, self.device))  # type: ignore[arg-type]

        rollout_buffer.compute_returns_and_advantage(
            last_values=values, dones=dones)

        callback.on_rollout_end()

        
        games_results.computeRates()
        
        
        self.my_record("rollout/success_rate", games_results.success_rate)
        self.my_record("rollout/timeout_rate", games_results.timeout_rate)
        self.my_record("rollout/goal_completion_rate", games_results.goal_completion_rate)
        self.my_record("rollout/mean_reward", games_results.mean_reward)
        self.my_record("rollout/mean_episode_length", games_results.mean_episode_length)
        self.my_record("rollout/mean_distance_reward", games_results.mean_distance_reward)
        self.my_record("rollout/mean_velocity_reward", games_results.mean_velocity_reward)
        self.my_record("rollout/mean_orientation_reward", games_results.mean_orientation_reward)
        self.my_record("rollout/mean_event_reward", games_results.mean_event_reward)
        self.my_record("rollout/first_goal_completion_rate", games_results.first_goal_completion_rate)
        self.my_record("rollout/second_goal_completion_rate", games_results.second_goal_completion_rate)
        self.my_record("rollout/third_goal_completion_rate", games_results.third_goal_completion_rate)

        self.my_record("rollout/completed_episodes", games_results.completed_episodes)

        step_average_wait_time = games_results.waitTime / total_timesteps
        self.my_record("rollout/step_average_wait_time", step_average_wait_time)
        self.my_record("rollout/rate_episodes_with_collisions", games_results.rate_episodes_with_collisions)
        self.my_record("rollout/avg_step_duration_unity", games_results.avg_step_duration_unity_env) # average duration of a step measured in unity episode duration time

        self.my_record("prescalerewards/mean_distance_reward", games_results.mean_prescale_distance_reward)
        self.my_record("prescalerewards/mean_velocity_reward", games_results.mean_prescale_velocity_reward)
        self.my_record("prescalerewards/mean_orientation_reward", games_results.mean_prescale_orientation_reward)
        self.my_record("prescalerewards/mean_event_reward", games_results.mean_prescale_event_reward)

        self.my_record("rollout_collisions/collision_rate", games_results.collision_rate)
        self.my_record("rollout_collisions/obstacle_collision_rate", games_results.obstacle_collision_rate)
        self.my_record("rollout_collisions/wall_collision_rate", games_results.wall_collision_rate)

        cr_time = time.time() - cr_time
        
        print(f'collect rollouts finished with {games_results.completed_episodes} episodes in {cr_time} seconds', flush=True)

        if games_results.success_rate >= self.rollout_best_success_rate:
            self.rollout_best_success_rate = games_results.success_rate
            if self.rollout_best_model_name != "":
                os.remove(f'{self.rollout_best_model_name}.zip')
            self.rollout_best_model_name = f'rollout_model_{int(games_results.success_rate*100)}-sr_{self.num_timesteps}-steps'
            self.save(self.rollout_best_model_name)

        return True, cr_time
    
    def my_record(self, key: str, value: float, exclude = None) -> None:
        x = self.num_timesteps
        if key not in self.my_logs.keys():
            self.my_logs[key] = {}

        self.my_logs[key][x] = value

        if exclude == None:
            self.logger.record(key, value)
        else: 
            self.logger.record(key, value, exclude=exclude)

    def my_dump(self, step: int, extralog: bool=False) -> None:
        for metric, dictionary in self.my_logs.items():

            prefix = metric.split("/")[0]

            if not os.path.exists(prefix):
                os.makedirs(prefix)

            file_path = f'{metric}.csv'
            #if extralog:
            #    print(f'print to {file_path}')

            with open(file_path, 'w', newline='') as file:
                writer = csv.writer(file)
                for timestep, value in dictionary.items():
                    writer.writerow([timestep, value])

                    #if extralog:
                    #    print(f'{timestep}: {value}')

        self.logger.dump(step=step)


    def train(self) -> None:
        """
        Consume current rollout data and update policy parameters.
        Implemented by individual algorithms.
        """
        raise NotImplementedError

    def learn(
        self: SelfOnPolicyAlgorithm,
        total_timesteps: int,
        callback: MaybeCallback = None,
        log_interval: int = 1,
        tb_log_name: str = "OnPolicyAlgorithm",
        reset_num_timesteps: bool = True,
        progress_bar: bool = False,
        num_evals_per_difficulty: int = 10,
        eval_light_settings: bool = False,
    ) -> SelfOnPolicyAlgorithm:
        iteration = 0
        

        print(f'num_evals_per_difficulty {num_evals_per_difficulty} eval_light_settings {eval_light_settings}')

        assert self.env is not None

        total_timesteps, callback = self._setup_learn(
            total_timesteps,
            callback,
            reset_num_timesteps,
            tb_log_name,
            progress_bar,
        )


        if self.print_network_and_loss_structure:
            self.print_network_structure(self.env)


        callback.on_training_start(locals(), globals())
        learn_starttime = time.time()

        total_cr_time, total_train_time, total_eval_time = 0, 0, 0
        self.collected_episodes = 0
        self.max_total_success_rate = -1

        self.rollout_best_success_rate = 0
        self.rollout_best_model_name = ""

        total_collection_time = 0

        while self.num_timesteps < total_timesteps:
            # collect_rollouts
            
            iteration += 1

            
            continue_training, cr_time = self.collect_rollouts(
                self.env, callback, self.rollout_buffer, n_rollout_steps=self.n_steps)
            
            total_collection_time += cr_time

            total_cr_time += cr_time

            if continue_training is False:
                break

            self._update_current_progress_remaining(
                self.num_timesteps, total_timesteps)


            # Display training infos
            if True: #log_interval is not None and iteration % log_interval == 0:
                # log interval is 0, thus after every ollect rollouts the tb logging is done
                # the log x axis is self.num_timesteps, which is modified in collect_rollouts


                assert self.ep_info_buffer is not None
                time_elapsed = max(
                    (time.time_ns() - self.start_time) / 1e9, sys.float_info.epsilon)
                fps = int(
                    (self.num_timesteps - self._num_timesteps_at_start) / time_elapsed)
                # what is fps?
                # num_timestep is increased in collect_rollouts for n_env after each step
                # The time/fps are thus distributed for the n_envs
                # computed in real-time not simulation time
                fps_per_env = float(fps / self.n_envs)

                # fps takes the time for the whole training, collect_rollout_fps_per_env only counts collection time
                collect_rollout_fps_per_env = float(((self.num_timesteps - self._num_timesteps_at_start) / self.n_envs ) / total_collection_time)

                self.my_record("time/iterations",
                                   iteration, exclude="tensorboard")
                '''if len(self.ep_info_buffer) > 0 and len(self.ep_info_buffer[0]) > 0:
                    self.logger.record(
                        "rollout/ep_rew_mean", safe_mean([ep_info["r"] for ep_info in self.ep_info_buffer]))
                    self.logger.record(
                        "rollout/ep_len_mean", safe_mean([ep_info["l"] for ep_info in self.ep_info_buffer]))'''
                self.my_record("time/fps", fps)
                self.my_record("time/fps_per_env", fps_per_env)
                self.my_record("time/collect_rollout_fps_per_env", collect_rollout_fps_per_env)

                self.my_record("time/time_elapsed",
                                   int(time_elapsed), exclude="tensorboard")
                self.my_record("time/total_timesteps",
                                   self.num_timesteps, exclude="tensorboard")
                self.my_record("rollout/collected_episodes", self.collected_episodes)

                self.my_record("time/collection_time_seconds", cr_time)
                self.my_record("time/iteration", iteration)
                self.my_record("time/timesteps_per_hour_realtime", self.num_timesteps / ((time.time()-learn_starttime) / 3600)) # this includes the train and eval time ...

                self.my_dump(step=self.num_timesteps)

            train_time = time.time()
            self.train()
            train_time = time.time() - train_time
            self.my_record("time/train_time_minutes", train_time / 60)
            self.my_dump(step=self.num_timesteps)
            
            total_train_time += train_time

            # model eval 
            if log_interval is not None and iteration % log_interval == 0:
                print(f'Will eval now as after every {log_interval} collect and trains')
                eval_time = time.time()
                self.eval(iteration=iteration, num_evals_per_difficulty=num_evals_per_difficulty, eval_light_settings=eval_light_settings)
                self.playGamesWithIdenticalStartConditions(n_episodes=10, iteration=iteration, light_setting=LightSetting.standard, log=True)
                self.test_deterministic_improves(n_episodes=10, difficulty="medium", iteration=iteration, light_setting=LightSetting.standard, log=True)
                self.test_fresh_obs_improves(n_episodes=10, difficulty = "medium", iteration=iteration, light_setting=LightSetting.standard, log=True)


                eval_time = time.time() - eval_time
                self.my_record("time/eval_time_seconds", eval_time)

                print(f'eval finished minutes: {eval_time / 60}')
                total_eval_time += eval_time
                
                self.my_dump(step=self.num_timesteps)

                print(f'total_cr_time: {total_cr_time}')
                print(f'total_train_time: {total_train_time}')
                print(f'total_eval_time: {total_eval_time}', flush=True)

        callback.on_training_end()

        return self

    def _get_torch_save_params(self) -> Tuple[List[str], List[str]]:
        state_dicts = ["policy", "policy.optimizer"]

        return state_dicts, []
    
    def test_deterministic_improves(self, n_episodes: int = 10, difficulty: str = "easy", iteration: int = 0, light_setting: LightSetting = LightSetting.standard, log =False) -> float:
        dirpath=f'{os.getcwd()}\\videos_iter_{iteration}'
        if not os.path.exists(dirpath):
            os.mkdir(dirpath)

        deter_success_rate, deter_collision_rate = self.eval_model_track(n_episodes, difficulty, iteration, light_setting, deterministic=True, log=False)
        nondeter_success_rate, nondeter_collision_rate = self.eval_model_track(n_episodes, difficulty, iteration, light_setting, deterministic=False, log=False)

        print(f'deter success rate: {deter_success_rate} collision rate: {deter_collision_rate}')
        print(f'nondeter success rate: {nondeter_success_rate} collision rate: {nondeter_collision_rate}')
        print(f'medium deter better than nondeter: {deter_success_rate > nondeter_success_rate}')

        # TODO atari/human_level_control paper uses epsilon greedy during evaluation to avoid overfitting, see paragraph Evaluation procedure. 
        # it might not be needed for our task, as we have different start rotations

        if log:
            self.my_record(f"deter_nondeter_comparison/success_deter_{difficulty}_{light_setting.name}", deter_success_rate)
            self.my_record(f"deter_nondeter_comparison/success_nondeter_{difficulty}_{light_setting.name}", nondeter_success_rate)
            self.my_record(f"deter_nondeter_comparison/collision_rate_deter_{difficulty}_{light_setting.name}", deter_collision_rate)
            self.my_record(f"deter_nondeter_comparison/collision_rate_nondeter_{difficulty}_{light_setting.name}", nondeter_collision_rate)

            self.my_record(f"deter_nondeter_comparison/nondeter_better_by_{difficulty}_{light_setting.name}", nondeter_success_rate - deter_success_rate)


    def test_fresh_obs_improves(self, n_episodes: int = 10, difficulty: str = "easy", iteration: int = 0, light_setting: LightSetting = LightSetting.standard, log=False) -> float:
        dirpath=f'{os.getcwd()}\\videos_iter_{iteration}'
        if not os.path.exists(dirpath):
            os.mkdir(dirpath)

        fresh_obs_status = self.use_fresh_obs


        self.use_fresh_obs = True
        fresh_obs_success_rate, fresh_obs_collision_rate = self.eval_model_track(n_episodes, difficulty, iteration, light_setting, deterministic=True, log=False)
        self.use_fresh_obs = False
        nonfresh_obs_success_rate, nonfresh_obs_collision_rate = self.eval_model_track(n_episodes, difficulty, iteration, light_setting, deterministic=False, log=False)

        self.use_fresh_obs = fresh_obs_status

        print(f'fresh obs success rate: {fresh_obs_success_rate} collision rate: {fresh_obs_collision_rate}')
        print(f'nonfresh obs success rate: {nonfresh_obs_success_rate} collision rate: {nonfresh_obs_collision_rate}')
        print(f'fresh better than nonfresh obs: {fresh_obs_success_rate > nonfresh_obs_success_rate}')
        if log:
            self.my_record(f"fresh_nonfresh_comparison/success_fresh_{difficulty}_{light_setting.name}", fresh_obs_success_rate)
            self.my_record(f"fresh_nonfresh_comparison/success_nonfresh_{difficulty}_{light_setting.name}", nonfresh_obs_success_rate)
            self.my_record(f"fresh_nonfresh_comparison/collision_rate_fresh_{difficulty}_{light_setting.name}", fresh_obs_collision_rate)
            self.my_record(f"fresh_nonfresh_comparison/collision_rate_nonfresh_{difficulty}_{light_setting.name}", nonfresh_obs_collision_rate)

            self.my_record(f"fresh_nonfresh_comparison/fresh_better_by_{difficulty}_{light_setting.name}", fresh_obs_success_rate - nonfresh_obs_success_rate)



    def eval(self: SelfOnPolicyAlgorithm, iteration: int = 0, num_evals_per_difficulty: int = 20, eval_light_settings: bool = False) -> float:
        print(f'eval started', flush=True)


        if eval_light_settings:
            light_settings = [LightSetting.bright, LightSetting.standard, LightSetting.dark]
        else: 
            light_settings = [LightSetting.standard]

        dirpath = f'{os.getcwd()}\\videos_iter_{iteration}'
        if not os.path.exists(dirpath):
            os.mkdir(dirpath)

        total_success_rate = 0
        total_collision_rate = 0

        avg_easy_success_rate, avg_medium_success_rate, avg_hard_success_rate = 0, 0, 0

        avg_easy_collision_rate, avg_medium_collision_rate, avg_hard_collision_rate = 0, 0, 0

        for light_setting in light_settings:
            
            easy_success_rate, easy_collision_rate = self.eval_model_track(n_eval_episodes = num_evals_per_difficulty, difficulty ="easy", iteration=iteration, light_setting=light_setting)
            medium_success_rate, medium_collision_rate = self.eval_model_track(n_eval_episodes =num_evals_per_difficulty, difficulty="medium", iteration=iteration, light_setting=light_setting)
            hard_success_rate, hard_collision_rate = self.eval_model_track(n_eval_episodes =num_evals_per_difficulty, difficulty="hard", iteration=iteration, light_setting=light_setting)
            total_success_rate += easy_success_rate + medium_success_rate + hard_success_rate
            light_success_rate = (easy_success_rate + medium_success_rate + hard_success_rate) / 3
            
            self.my_record(f"eval/success_easy_{light_setting.name}", easy_success_rate)
            self.my_record(f"eval/success_medium_{light_setting.name}", medium_success_rate)
            self.my_record(f"eval/success_hard_{light_setting.name}", hard_success_rate)
            self.my_record(f"eval/success_{light_setting.name}", light_success_rate)

            total_collision_rate += easy_collision_rate + medium_collision_rate + hard_collision_rate
            light_collision_rate = (easy_collision_rate + medium_collision_rate + hard_collision_rate) / 3
            self.my_record(f"eval_collision_rates/collision_rate_easy_{light_setting.name}", easy_collision_rate)
            self.my_record(f"eval_collision_rates/collision_rate_medium_{light_setting.name}", medium_collision_rate)
            self.my_record(f"eval_collision_rates/collision_rate_hard_{light_setting.name}", hard_collision_rate)
            self.my_record(f"eval_collision_rates/collision_rate_{light_setting.name}", light_collision_rate)


            avg_easy_success_rate += easy_success_rate
            avg_medium_success_rate += medium_success_rate
            avg_hard_success_rate += hard_success_rate

            avg_easy_collision_rate += easy_collision_rate
            avg_medium_collision_rate += medium_collision_rate
            avg_hard_collision_rate += hard_collision_rate

        if eval_light_settings:
            self.my_record(f"eval/success_easy_across_light_settings", avg_easy_success_rate / len(light_settings))
            self.my_record(f"eval/success_medium_across_light_settings", avg_medium_success_rate / len(light_settings))
            self.my_record(f"eval/success_hard_across_light_settings", avg_hard_success_rate / len(light_settings))

            self.my_record(f"eval_important/success_easy_across_light_settings", avg_easy_success_rate / len(light_settings))
            self.my_record(f"eval_important/success_medium_across_light_settings", avg_medium_success_rate / len(light_settings))
            self.my_record(f"eval_important/success_hard_across_light_settings", avg_hard_success_rate / len(light_settings))

            self.my_record(f"eval_collision_rates/collision_rate_easy_across_light_settings", avg_easy_collision_rate / len(light_settings))
            self.my_record(f"eval_collision_rates/collision_rate_medium_across_light_settings", avg_medium_collision_rate / len(light_settings))
            self.my_record(f"eval_collision_rates/collision_rate_hard_across_light_settings", avg_hard_collision_rate / len(light_settings))

        total_success_rate = total_success_rate / (3 * len(light_settings))
        if total_success_rate > self.max_total_success_rate:
            self.max_total_success_rate = total_success_rate
            self.best_model_name = f"best_model_episode_{iteration}"
            self.save(self.best_model_name)
        
        total_collision_rate = (total_collision_rate) / (3 * len(light_settings))
        


        self.my_record("eval/success_rate", total_success_rate)
        self.my_record("eval_important/success_rate", total_success_rate)

        self.my_record("eval/collision_rate", total_collision_rate)
        self.my_record("eval_collision_rates/collision_rate", total_collision_rate)

        return total_success_rate
    
    def generate_map_and_rotations(self, difficulty: str, n_eval_episodes: int, env: VecEnv) -> List[Tuple[str, List[int]]]:
        
        rotationMode = env.env_method(
            method_name="getSpawnMode",
            indices=[0]
        )[0]

        rotation_range_min, rotation_range_max = Spawn.getOrientationRange(rotationMode)

        range_width = rotation_range_max - rotation_range_min
        step = range_width / (n_eval_episodes-1)

        rotations = [rotation_range_min + i * step for i in range(n_eval_episodes)]
        rotations = [int(rotation) for rotation in rotations]

        track_numbers = MapType.getAllTracksnumbersOfDifficulty(difficulty)

        tracks = []
        for i in range(n_eval_episodes):
            track = track_numbers[i % len(track_numbers)]
            tracks.append(MapType(track))

        # and example of the resulting track and rotation combinations:
        # map_and_rotations: [(<MapType.hardBlueFirstLeft: 7>, -15), (<MapType.hardBlueFirstRight: 8>, -13), (<MapType.hardRedFirstLeft: 9>, -11), (<MapType.hardRedFirstRight: 10>, -10), (<MapType.hardBlueFirstLeft: 7>, -8), (<MapType.hardBlueFirstRight: 8>, -7), (<MapType.hardRedFirstLeft: 9>, -5), (<MapType.hardRedFirstRight: 10>, -3), (<MapType.hardBlueFirstLeft: 7>, -2), (<MapType.hardBlueFirstRight: 8>, 0), (<MapType.hardRedFirstLeft: 9>, 0), (<MapType.hardRedFirstRight: 10>, 2), (<MapType.hardBlueFirstLeft: 7>, 3), (<MapType.hardBlueFirstRight: 8>, 5), (<MapType.hardRedFirstLeft: 9>, 7), (<MapType.hardRedFirstRight: 10>, 8), (<MapType.hardBlueFirstLeft: 7>, 10), (<MapType.hardBlueFirstRight: 8>, 11), (<MapType.hardRedFirstLeft: 9>, 13), (<MapType.hardRedFirstRight: 10>, 15)]

        return list(zip(tracks, rotations))

    def eval_model_track(
        self: SelfOnPolicyAlgorithm,
        n_eval_episodes: int = 10,
        difficulty: str = "easy",
        iteration: int = 0,
        light_setting: LightSetting = LightSetting.standard,
        deterministic: bool = False,
        log=True
    ):
        # all maps from the difficulty setting are selected with the same proportion
        # the JetBot spawn rotation depends on the spawn_pos in the config, e.g. OrientationRandom
        # the spawn rotation interval is divided into equal parts, these different values are used to spawn the jetbot

        # this results in identical spawn positions/rotations and maps for a particular set of function parameters

        map_and_rotations = self.generate_map_and_rotations(difficulty, n_eval_episodes, self.env)
        map_and_rotations_counter = 0
        #print(f'map_and_rotations: {map_and_rotations}', flush=True)

        env = self.env
        n_envs = env.num_envs
        episode_rewards = []
        episode_lengths = []
        finished_episodes = 0
        
        games_results = GamesResults()


        episode_counts = np.zeros(n_envs, dtype="int")
        # Divides episodes among different sub environments in the vector as evenly as possible
        episode_count_targets = np.array([(n_eval_episodes + i) // n_envs for i in range(n_envs)], dtype="int")
        # episode_count_targets represents the amount of episodes that have to be played in the corresponding env
        # the sum of these values is equal to n_eval_episodes

        current_rewards = np.zeros(n_envs)
        current_lengths = np.zeros(n_envs, dtype="int")


        # reset environment 0 to record the videos
        log_indices = [0, 1] # these indices will record videos
        for i in log_indices:
            env.env_method(
                method_name="setVideoFilename",
                indices=[i],
                video_filename = f'{os.getcwd()}\\videos_iter_{iteration}\\{difficulty}_{light_setting.name}_env_{i}_video_'
            )
        

        amount_of_envs_first_run = min(n_envs, n_eval_episodes)
        for i in range(amount_of_envs_first_run):
            env.env_method(
                method_name="reset",
                indices=[i],
                mapType=map_and_rotations[map_and_rotations_counter][0],
                lightSetting=light_setting,
                evalMode=True,
                spawnRot = map_and_rotations[map_and_rotations_counter][1],
            )
            map_and_rotations_counter += 1


        # switch to eval mode
        self.policy.set_training_mode(False)

        if not self.use_fresh_obs:
            # get the first observations
            if self.use_bundled_calls:
                obs = get_obs_bundled_calls(env)
            else:
                obs = get_obs_single_calls(env)
            self._last_obs = obs

        while (episode_counts < episode_count_targets).any():
    
            #assert False, "do we have to use deteministic forward here?"
            actions, values, log_probs, obs = self.inferFromObservations(env, deterministic)
            actions = actions.cpu().numpy()

            # Rescale and perform action
            clipped_actions = actions
            # Clip the actions to avoid out of bound error
            if isinstance(self.action_space, spaces.Box):
                clipped_actions = np.clip(
                    actions, self.action_space.low, self.action_space.high)
            
            observations, rewards, dones, infos = step_wrapper(env, clipped_actions, self.use_bundled_calls)

            
            current_lengths += 1
            for i in range(n_envs):
                if episode_counts[i] < episode_count_targets[i]:

                    if dones[i]:
                        
                        episode_rewards.append(float(infos[i]["cumreward"].replace(",",".")))
                        episode_lengths.append(current_lengths[i])
                        episode_counts[i] += 1
                        current_rewards[i] = 0
                        current_lengths[i] = 0
                        finished_episodes += 1

                        games_results.processInfoDictEpisodeFinished(infos[i])

                        if i in log_indices:
                            if episode_counts[i] == episode_count_targets[i]-1:
                                # no more logging needed for this env
                                env.env_method(
                                    method_name="setVideoFilename",
                                    indices=[i],
                                    video_filename = ""
                                )

                        # reset if we still need more runs for that environment
                        if episode_counts[i] < episode_count_targets[i]:
                            env.env_method(
                                method_name="reset",
                                indices=[i],
                                mapType=map_and_rotations[map_and_rotations_counter][0],
                                lightSetting=light_setting,
                                evalMode=True,
                                spawnRot = map_and_rotations[map_and_rotations_counter][1],
                            )
                            map_and_rotations_counter += 1
            
            self._last_obs = observations

        assert np.sum(episode_counts) == n_eval_episodes, f"not all episodes were finished, {np.sum(episode_counts)} != {n_eval_episodes}"
        assert finished_episodes == n_eval_episodes, f"not all episodes were finished, {finished_episodes} != {n_eval_episodes}"
        assert map_and_rotations_counter == n_eval_episodes, f"not all maps were used, {map_and_rotations_counter} != {len(map_and_rotations)}"

        mean_reward = np.mean(episode_rewards)
        std_reward = np.std(episode_rewards)
        
        games_results.computeRates()

        if log: 
            self.my_record(f'eval_{difficulty}_{light_setting.name}/mean_reward', mean_reward)
            
            self.my_record(f'eval_{difficulty}_{light_setting.name}/std_reward', std_reward)
            self.my_record(f'eval_{difficulty}_{light_setting.name}/success_rate', games_results.success_rate)
            self.my_record(f'eval_{difficulty}_{light_setting.name}/rate_passed_goals', games_results.goal_completion_rate)
            self.my_record(f'eval_{difficulty}_{light_setting.name}/timeout_rate', games_results.timeout_rate)
            self.my_record(f'eval_{difficulty}_{light_setting.name}/rate_episode_with_collision', games_results.collision_episodes / n_eval_episodes)

            self.my_record(f'eval_{difficulty}_{light_setting.name}/rate_first_goal', games_results.first_goal_completion_rate)
            self.my_record(f'eval_{difficulty}_{light_setting.name}/rate_second_goal', games_results.second_goal_completion_rate)
            self.my_record(f'eval_{difficulty}_{light_setting.name}/rate_third_goal', games_results.third_goal_completion_rate)

            step_average_wait_time = games_results.waitTime / np.sum(episode_lengths)
            self.my_record(f"eval_{difficulty}_{light_setting.name}/step_average_wait_time", step_average_wait_time)
            self.my_record(f'eval_{difficulty}_{light_setting.name}/average_episode_length', np.average(episode_lengths))

        # set to no video afterwards
        for index in log_indices:
            env.env_method(
                method_name="setVideoFilename",
                indices=[index],
                video_filename = ""
            )

        return games_results.success_rate, games_results.collision_rate
    

    def eval_only(
        self: SelfOnPolicyAlgorithm,
        total_eval_runs: int,
        callback: MaybeCallback = None,
        log_interval: int = 1,
        tb_log_name: str = "OnPolicyAlgorithm",
        reset_num_timesteps: bool = True,
        progress_bar: bool = False,
        num_evals_per_difficulty: int = 10,
        eval_light_settings: bool = False,
        offset: int = 0,
    ) -> SelfOnPolicyAlgorithm:
        iteration = 0

        
        from stable_baselines3.common import utils
        self._logger = utils.configure_logger(self.verbose, self.tensorboard_log, tb_log_name, reset_num_timesteps)

        assert self.env is not None

        total_eval_time = 0

        for i in range(total_eval_runs):
            step = offset + i*1000
            self.num_timesteps = step # for proper logging we need to manipulate this, very dirty!!!

            eval_time = time.time()
            self.eval(iteration=step, num_evals_per_difficulty=num_evals_per_difficulty, eval_light_settings=eval_light_settings)
            self.playGamesWithIdenticalStartConditions(n_episodes=10, iteration=step, light_setting=LightSetting.standard, deterministic=True, log=True)
            self.test_deterministic_improves(n_episodes=10, difficulty = "medium", iteration=step, light_setting=LightSetting.standard, log=True)
            self.test_fresh_obs_improves(n_episodes=10, difficulty = "medium", iteration=step, light_setting=LightSetting.standard, log=True)


            eval_time = time.time() - eval_time
            self.my_record("time/eval_time_seconds", eval_time)

            print(f'eval finished minutes: {eval_time / 60}')
            total_eval_time += eval_time

            self.my_dump(step=step, extralog=False)

            print(f'total_eval_time: {total_eval_time}', flush=True)

        return self
    
    def invariant_output_test(self):
        env = self.env
        env.reset()

        #self.policy.set_training_mode(False)

        with th.no_grad():
            obs = get_obs_bundled_calls(env)
        
            obs_tensor = obs_as_tensor(obs, self.device)
            
            actions, values, log_probs = self.policy(obs_tensor, deterministic = True)
            
            # now we repeat for the first observation alone
            first_obs = obs[0:1]
            first_obs_tensor = obs_as_tensor(first_obs, self.device)


            first_actions, first_values, first_log_probs = self.policy(first_obs_tensor, deterministic = True)

            assert th.allclose(actions[0:1], first_actions), f'actions are not invariant to the batch size/samples {actions[0:1]} != {first_actions}'
            print(f'actions are invariant to the batch size/samples')


    def playGamesWithIdenticalStartConditions(
        self: SelfOnPolicyAlgorithm,
        n_episodes: int = 10,
        iteration: int = 0,
        light_setting: LightSetting = LightSetting.standard,
        deterministic: bool = True,
        log=False
    ):
        # same initialization of envs, does the agent traverse the env in the same way?

        env = self.env
        n_envs = env.num_envs
        episode_rewards = []
        episode_lengths = []
        success_count, finished_episodes = 0, 0
       
        # game results are characterized by endEvent, collision, passedFirstGoal, passedSecondGoal, passedThirdGoal

        game_results = []


        episode_counts = np.zeros(n_envs, dtype="int")
        # Divides episodes among different sub environments in the vector as evenly as possible
        episode_count_targets = np.array([(n_episodes + i) // n_envs for i in range(n_envs)], dtype="int")
        # episode_count_targets represents the amount of episodes that have to be played in the corresponding env
        # the sum of these values is equal to n_eval_episodes

        current_rewards = np.zeros(n_envs)
        current_lengths = np.zeros(n_envs, dtype="int")

        dirpath = f'{os.getcwd()}\\videos_identicalStartConditions_iter_{iteration}'
        if not os.path.exists(dirpath):
            os.mkdir(dirpath)

        # reset environment 0 to record the videos
        log_indices = [0, 1] # these indices will record videos
        for i in log_indices:
            env.env_method(
                method_name="setVideoFilename",
                indices=[i],
                video_filename = f'{os.getcwd()}\\videos_identicalStartConditions_iter_{iteration}\\{light_setting.name}_env_{i}_video_'
            )

        # reset all envs with one specific map and rotation
        env.env_method(
            method_name="reset_with_mapType_spawnrotation",
            indices=range(n_envs),
            mapType=MapType.mediumBlueFirstRight,
            lightSetting=light_setting,
            evalMode=True,
            spawnRot=0
        ) 

        # switch to eval mode
        self.policy.set_training_mode(False)

        if not self.use_fresh_obs:
            # get the first observations
            if self.use_bundled_calls:
                obs = get_obs_bundled_calls(env)
            else:
                obs = get_obs_single_calls(env)
            self._last_obs = obs


        while (episode_counts < episode_count_targets).any():
    
            actions, values, log_probs, obs = self.inferFromObservations(env, deterministic=deterministic)
            actions = actions.cpu().numpy()

            # Rescale and perform action
            clipped_actions = actions
            # Clip the actions to avoid out of bound error
            if isinstance(self.action_space, spaces.Box):
                clipped_actions = np.clip(
                    actions, self.action_space.low, self.action_space.high)
            
            observations, _, dones, infos = step_wrapper(env, clipped_actions, self.use_bundled_calls)
            
            current_lengths += 1
            for i in range(n_envs):
                if episode_counts[i] < episode_count_targets[i]:

                    if dones[i]:

                        episode_rewards.append(float(infos[i]["cumreward"].replace(",",".")))
                        episode_lengths.append(current_lengths[i])
                        episode_counts[i] += 1
                        current_rewards[i] = 0
                        current_lengths[i] = 0
                        finished_episodes += 1


                        if infos[i]["endEvent"] == "Success":
                            success_count += 1

                        game_results.append(GameRepresentation(infos[i]))

                        if i in log_indices:
                            if episode_counts[i] == episode_count_targets[i]-1:
                                # no more logging needed for this env
                                env.env_method(
                                    method_name="setVideoFilename",
                                    indices=[i],
                                    video_filename = ""
                                )


                        # due to auto reset we have to reset the env again with the right parameters:
                        env.env_method(
                            method_name="reset_with_mapType_spawnrotation",
                            indices=[i],
                            mapType=MapType.mediumBlueFirstRight,
                            lightSetting=light_setting,
                            evalMode=True,
                            spawnRot=0
                        )
            
            self._last_obs = observations

        assert np.sum(episode_counts) == n_episodes, f"not all episodes were finished, {np.sum(episode_counts)} != {n_episodes}"
        assert finished_episodes == n_episodes, f"not all episodes were finished, {finished_episodes} != {n_episodes}"
        
        success_rate = success_count / n_episodes


        game_results_counter = collections.Counter(game_results)

        most_common_game_result = max(set(game_results), key=game_results.count)
        print(f'most common game result: {most_common_game_result}')

        print(f'game results and counts: {game_results_counter}')

        most_common_game_result_rate = game_results_counter[most_common_game_result] / n_episodes
        print(f'deterministic={deterministic} rate of most common game result: {most_common_game_result_rate}')
        
        
        # set to no video afterwards
        for index in log_indices:
            env.env_method(
                method_name="setVideoFilename",
                indices=[index],
                video_filename = ""
            )

        print(f'playGamesWithIdenticalStartConditions finished', flush=True)

        if log:
            self.my_record(f'identicalStartConditions/most_common_game_result_rate', most_common_game_result_rate)

        return most_common_game_result_rate, success_rate

def get_obs_single_calls(env):
    # env is a vectorized BaseCarsimEnv
    # it is wrapped in a vec_transpose env for the CNN

    
    # TODO this does a memory rollover, as well as the step function
    # should one of them not do it? or does it not matter?
    # the frequent rollovers might result in the history not being deep enough

    # I think the cleanest way to solve this is by simply increasing frame stacking n

    for idx in range(env.num_envs):
        obs = env.envs[idx].get_observation_including_memory()
        # get_obseration_including memory does a memory rolloer as well
        env._save_obs(idx, obs)

    obs = env._obs_from_buf()
    return env.transpose_observations(obs)

def get_obs_bundled_calls(env):
    # env is a vectorized BaseCarsimEnv
    # it is wrapped in a vec_transpose env for the CNN

    
    # TODO this does a memory rollover, as well as the step function
    # should one of them not do it? or does it not matter?
    # the frequent rollovers might result in the history not being deep enough

    # I think the cleanest way to solve this is by simply increasing frame stacking n

    all_obsstrings = env.envs[0].get_obsstrings_with_single_request()

    #print(f'all_obsstrings type and length: {type(all_obsstrings)} {len(all_obsstrings)}')

    assert len(all_obsstrings) == env.num_envs, f"all_obsstrings has wrong length {len(all_obsstrings)} != {env.num_envs}"

    all_observations = []
    for i in range(len(all_obsstrings)):
        obs = env.envs[i].stringToObservation(all_obsstrings[i])
        if env.envs[i].frame_stacking > 1:
            obs = env.envs[i].memory_rollover(obs)
        all_observations.append(obs)

    for idx in range(env.num_envs):
        # get_obseration_including memory does a memory rolloer as well

        
        env._save_obs(idx, all_observations[idx])

    #print(f'get_obs_bundled_calls observations shape: {all_observations[0].shape} {type(all_observations[0])}')
    # TODO does the _save_obs change the dimensions?
    # or the transpose_observations?

    obs = env._obs_from_buf()
    #print(f'get_obs_bundled_calls observations from buf shape: {obs.shape} {type(obs)}')
    rtn_obs = env.transpose_observations(obs)
    

    #print(f'get_obs_bundled_calls observations transposed shape: {rtn_obs.shape} {type(rtn_obs)}')
    
    return rtn_obs

def step_wrapper(env, clipped_actions, use_bundled_calls):

    if use_bundled_calls:
        #print(f'step with single request started', flush=True)

        step_nrs = [env.envs[i].step_nr for i in range(env.num_envs)]
        left_actions = [float(clipped_actions[i][0]) for i in range(env.num_envs)]
        right_actions = [float(clipped_actions[i][1]) for i in range(env.num_envs)]

        stepReturnObjects = env.envs[0].bundledStep(step_nrs = step_nrs, left_actions=left_actions, right_actions=right_actions)
        # first do the bundled request to unity

        # TODO new_obs muss ein Tensor sein, keine liste
        #new_obs = []
        rewards = []
        dones = []
        truncateds = []
        infos = []

        rtn_new_obs_n = np.zeros((env.num_envs, *env.observation_space.shape))
        #print(f'shape new rtn obs: {rtn_new_obs_n.shape} {type(rtn_new_obs_n)}', flush=True)
        for idx in range(env.num_envs):
            # give the results to the corresponding envs
            # print(f'stepReturnObjects[idx]: {stepReturnObjects[idx]}', flush=True)

            new_ob, reward, done, truncated, info = env.envs[idx].processStepReturnObject(stepReturnObjects[idx])
            
            new_ob_transposed = env.transpose_observations(new_ob)
            rtn_new_obs_n[idx] = new_ob_transposed

            #new_obs.append(new_ob)
            rewards.append(reward)
            dones.append(done)
            truncateds.append(truncated)
            infos.append(info)

        #rtn_new_obs = np.array(new_obs)
        #print(f'shape new obs: {rtn_new_obs.shape} {type(rtn_new_obs)}', flush=True)

        #transpose_obs = env.transpose_observations(rtn_new_obs)
        #print(f'shape transposed obs: {transpose_obs.shape} {type(transpose_obs)}', flush=True)

        #assert np.array_equal(transpose_obs, rtn_new_obs_n), f'new obs not equal {transpose_obs} {rtn_new_obs_n}'

        return rtn_new_obs_n, np.array(rewards), np.array(dones), infos
    else:
        # old approach
        return env.step(clipped_actions)
    
