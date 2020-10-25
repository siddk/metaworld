import numpy as np
from gym.spaces import Box
from scipy.spatial.transform import Rotation

from metaworld.envs import reward_utils
from metaworld.envs.asset_path_utils import full_v2_path_for
from metaworld.envs.mujoco.sawyer_xyz.sawyer_xyz_env import SawyerXYZEnv, _assert_task_is_set


class SawyerShelfPlaceEnvV2(SawyerXYZEnv):

    def __init__(self):

        liftThresh = 0.04
        goal_low = (-0.1, 0.8, 0.299)
        goal_high = (0.1, 0.9, 0.301)
        hand_low = (-0.5, 0.40, 0.05)
        hand_high = (0.5, 1, 0.5)
        obj_low = (-0.1, 0.5, 0.019)
        obj_high = (0.1, 0.6, 0.021)

        super().__init__(
            self.model_name,
            hand_low=hand_low,
            hand_high=hand_high,
        )

        self.init_config = {
            'obj_init_pos':np.array([0, 0.6, 0.02]),
            'obj_init_angle': 0.3,
            'hand_init_pos': np.array([0, 0.6, 0.2], dtype=np.float32),
        }
        self.goal = np.array([0., 0.85, 0.301], dtype=np.float32)
        self.obj_init_pos = self.init_config['obj_init_pos']
        self.obj_init_angle = self.init_config['obj_init_angle']
        self.hand_init_pos = self.init_config['hand_init_pos']

        self.liftThresh = liftThresh
        self.max_path_length = 200
        self.num_resets = 0

        self._random_reset_space = Box(
            np.hstack((obj_low, goal_low)),
            np.hstack((obj_high, goal_high)),
        )
        self.goal_space = Box(np.array(goal_low), np.array(goal_high))

    @property
    def model_name(self):
        return full_v2_path_for('sawyer_xyz/sawyer_shelf_placing.xml')

    @property
    def touching_object(self):
        object_geom_id = self.unwrapped.model.geom_name2id('objGeom')
        leftpad_geom_id = self.unwrapped.model.geom_name2id('leftpad_geom')
        rightpad_geom_id = self.unwrapped.model.geom_name2id('rightpad_geom')

        leftpad_object_contacts = [
            x for x in self.unwrapped.data.contact
            if (leftpad_geom_id in (x.geom1, x.geom2)
                and object_geom_id in (x.geom1, x.geom2))
        ]

        rightpad_object_contacts = [
            x for x in self.unwrapped.data.contact
            if (rightpad_geom_id in (x.geom1, x.geom2)
                and object_geom_id in (x.geom1, x.geom2))
        ]

        leftpad_object_contact_force = sum(
            self.unwrapped.data.efc_force[x.efc_address]
            for x in leftpad_object_contacts)

        rightpad_object_contact_force = sum(
            self.unwrapped.data.efc_force[x.efc_address]
            for x in rightpad_object_contacts)

        gripping = (0 < leftpad_object_contact_force
                    and 0 < rightpad_object_contact_force)

        return gripping


    @_assert_task_is_set
    def step(self, action):
        obs = super().step(action)
        obj = obs[4:7]
        # reward, _, reachDist, pickRew, _, placingDist = self.compute_reward(action, obs)
        reward, tcp_to_obj, tcp_open, obj_to_target, grasp_reward, in_place = self.compute_reward(action, obs)
        success = float(obj_to_target <= 0.07)
        near_object = float(tcp_to_obj <= 0.03)
        grasp_success = float(self.touching_object and (tcp_open > 0) and (obj[2] - 0.02 > self.obj_init_pos[2]))

        info = {
            'success': success,
            'near_object': near_object,
            'grasp_success': grasp_success,
            'grasp_reward': grasp_reward,
            'in_place_reward': in_place,
            'obj_to_target': obj_to_target,
            'unscaled_reward': reward,

        }
        self.curr_path_length += 1


        return obs, reward, False, info

    def _get_pos_objects(self):
        return self.get_body_com('obj')

    def _get_pos_orientation_objects(self):
        position = self.get_body_com('obj')
        orientation = Rotation.from_matrix(
            self.data.get_geom_xmat('objGeom')).as_quat()
        return position, orientation, np.array([]), np.array([])

    def adjust_initObjPos(self, orig_init_pos):
        # This is to account for meshes for the geom and object are not aligned
        # If this is not done, the object could be initialized in an extreme position
        diff = self.get_body_com('obj')[:2] - self.get_body_com('obj')[:2]
        adjustedPos = orig_init_pos[:2] + diff

        #The convention we follow is that body_com[2] is always 0, and geom_pos[2] is the object height
        return [adjustedPos[0], adjustedPos[1],self.get_body_com('obj')[-1]]

    def reset_model(self):
        self._reset_hand()
        self.sim.model.body_pos[self.model.body_name2id('shelf')] = self.goal.copy() - np.array([0, 0, 0.3])
        self._target_pos = self.sim.model.site_pos[self.model.site_name2id('goal')] + self.sim.model.body_pos[self.model.body_name2id('shelf')]
        self.obj_init_pos = self.adjust_initObjPos(self.init_config['obj_init_pos'])
        self.obj_init_angle = self.init_config['obj_init_angle']
        self.objHeight = self.get_body_com('obj')[2]
        self.heightTarget = self.objHeight + self.liftThresh

        if self.random_init:
            goal_pos = self._get_state_rand_vec()
            while np.linalg.norm(goal_pos[:2] - goal_pos[-3:-1]) < 0.1:
                goal_pos = self._get_state_rand_vec()
            base_shelf_pos = goal_pos - np.array([0, 0, 0, 0, 0, 0.3])
            self.obj_init_pos = np.concatenate((base_shelf_pos[:2], [self.obj_init_pos[-1]]))
            self.sim.model.body_pos[self.model.body_name2id('shelf')] = base_shelf_pos[-3:]
            self._target_pos = self.sim.model.site_pos[self.model.site_name2id('goal')] + self.sim.model.body_pos[self.model.body_name2id('shelf')]

        self._set_obj_xyz(self.obj_init_pos)
        self.maxPlacingDist = np.linalg.norm(np.array([self.obj_init_pos[0], self.obj_init_pos[1], self.heightTarget]) - np.array(self._target_pos)) + self.heightTarget
        self.target_reward = 1000*self.maxPlacingDist + 1000*2
        self.num_resets += 1

        return self._get_obs()


    def _reset_hand(self):
        super()._reset_hand()

        rightFinger, leftFinger = self._get_site_pos('rightEndEffector'), self._get_site_pos('leftEndEffector')
        self.init_fingerCOM  =  (rightFinger + leftFinger)/2
        self.pickCompleted = False

    def compute_reward(self, action, obs):
            _TARGET_RADIUS = 0.05
            tcp = self.tcp_center
            obj = obs[4:7]
            tcp_opened = obs[3]
            target = self._target_pos

            obj_to_target = np.linalg.norm(obj - target)
            tcp_to_obj = np.linalg.norm(obj - tcp)
            in_place_margin = (np.linalg.norm(self.obj_init_pos - target))
            in_place = reward_utils.tolerance(obj_to_target,
                                        bounds=(0, _TARGET_RADIUS),
                                        margin=in_place_margin,
                                        sigmoid='long_tail',)

            object_grasped = self._gripper_caging_reward(action, obj, 0.02)
            in_place_and_object_grasped = reward_utils.hamacher_product(object_grasped,
                                                                        in_place)
            reward = in_place_and_object_grasped

            if tcp_to_obj < 0.02 and (tcp_opened > 0) and (obj[2] - 0.01 > self.obj_init_pos[2]):
                reward += 1. + 5. * in_place
            if obj_to_target < _TARGET_RADIUS:
                reward = 10.
            return [reward, tcp_to_obj, tcp_opened, obj_to_target, object_grasped, in_place]


        # objPos = obs[3:6]
        #
        # rightFinger, leftFinger = self._get_site_pos('rightEndEffector'), self._get_site_pos('leftEndEffector')
        # fingerCOM  =  (rightFinger + leftFinger)/2
        #
        # heightTarget = self.heightTarget
        # placingGoal = self._target_pos
        #
        # reachDist = np.linalg.norm(objPos - fingerCOM)
        #
        # placingDist = np.linalg.norm(objPos - placingGoal)
        #
        #
        # def reachReward():
        #     reachRew = -reachDist
        #     reachDistxy = np.linalg.norm(objPos[:-1] - fingerCOM[:-1])
        #     zRew = np.linalg.norm(fingerCOM[-1] - self.init_fingerCOM[-1])
        #
        #     if reachDistxy < 0.05:
        #         reachRew = -reachDist
        #     else:
        #         reachRew =  -reachDistxy - 2*zRew
        #
        #     # incentive to close fingers when reachDist is small
        #     if reachDist < 0.05:
        #         reachRew = -reachDist + max(actions[-1],0)/50
        #     return reachRew , reachDist
        #
        # def pickCompletionCriteria():
        #     tolerance = 0.01
        #     return objPos[2] >= (heightTarget- tolerance)
        #
        # self.pickCompleted = pickCompletionCriteria()
        #
        #
        # def objDropped():
        #     return (objPos[2] < (self.objHeight + 0.005)) and (placingDist >0.02) and (reachDist > 0.02)
        #     # Object on the ground, far away from the goal, and from the gripper
        #     # Can tweak the margin limits
        #
        # def orig_pickReward():
        #     hScale = 100
        #     if self.pickCompleted and not(objDropped()):
        #         return hScale*heightTarget
        #     elif (reachDist < 0.1) and (objPos[2]> (self.objHeight + 0.005)):
        #         return hScale* min(heightTarget, objPos[2])
        #     else:
        #         return 0
        #
        # def placeReward():
        #     c1 = 1000
        #     c2 = 0.01
        #     c3 = 0.001
        #     cond = self.pickCompleted and (reachDist < 0.1) and not(objDropped())
        #     if cond:
        #         placeRew = 1000*(self.maxPlacingDist - placingDist) + c1*(np.exp(-(placingDist**2)/c2) + np.exp(-(placingDist**2)/c3))
        #         placeRew = max(placeRew,0)
        #         return [placeRew , placingDist]
        #     else:
        #         return [0 , placingDist]
        #
        # reachRew, reachDist = reachReward()
        # pickRew = orig_pickReward()
        # placeRew , placingDist = placeReward()
        # assert ((placeRew >=0) and (pickRew>=0))
        # reward = reachRew + pickRew + placeRew
        #
        # return [reward, reachRew, reachDist, pickRew, placeRew, placingDist]
