from tdmclient import ClientAsync, aw
import numpy as np
import math
import time

class LocalNavigator:
    def __init__(self):
        self.client = ClientAsync()
        self.node = aw(self.client.wait_for_node())

        aw(self.node.lock())
        aw(self.node.wait_for_variables())

        self.dist_threshold = 2300 # 1600
        self.motor_speed = 200
        self.sensor_vals = list(self.node['prox.horizontal'])
        self.verbose = False # whether to print status message or not
        self.threshold = self.val_to_angle(6) # threshold value for global path (given angle)
        self.angle = self.val_to_angle(0) # the current rotated angle of Thymio
        self.cumulative_angle = self.val_to_angle(0) # the cumulative rotated angles
        self.turn_direction = 1 # turn right: 1, turn left: -1
        self.start = 0 # start time to rotate
        self.end = 0 # end time to rotate
        self.time = 0 # rotation time
        self.omega = 0 # rotation velocity
        """
        |Degree|Motor speed|velocity (degree/sec)|
        |------|-----------|---------------------|
        | 1080 |    300    |         108         |
        | 554  |    200    |         55          |
        | 370  |    100    |         37          |
        | 325  |    80     |         32          |
        | 222  |    50     |         22          |
        | 135  |    30     |         13          |
        """
        self.is_alter = [] # for checking whether Thymio stuck in deadlock
        self.deadlock_flag = False # whether Thymio stuck in deadlock
        self.reflected_sensor_vals = list(self.node['prox.ground.reflected']) # {0...1023}
        self.height_threshold = 70
        self.kidnap = False
        self.resolvedObstacle = False

    def get_outputs(self):
        return (self.angle, self.cumulative_angle, self.motor_speed)

    def update_proxsensor(self):
        self.sensor_vals = list(self.node["prox.horizontal"])

    def val_to_angle(self, val):
        return val * math.pi / 180

    def motor(self, l_speed=500, r_speed=500):
        if self.verbose:
            print("\tSetting speed: ", l_speed, r_speed)
        return {
                "motor.left.target": [l_speed],
                "motor.right.target": [r_speed],
                }

    def print_sensor_values(self):
        if self.verbose:
            print("\tSensor values (prox_horizontal): ", self.sensor_vals)

    def compute_angle(self):
        self.angle = self.turn_direction * self.omega * self.time
        self.cumulative_angle += self.angle
        if self.verbose:
            print("Rotated angle: ", self.angle)
            print("Cumulative rotated angle: ", self.cumulative_angle)

    def compute_motor_speed(self):
        max_sensor_val = max(self.sensor_vals)

        if 0 <= max_sensor_val < 1500:
            self.motor_speed = 200
            self.omega = 55
        elif 1500 <= max_sensor_val < 2000:
            self.motor_speed = 100
            self.omega = 37
        elif 2000 <= max_sensor_val < 2300:
            self.motor_speed = 80
            self.omega = 32
        elif 2300 <= max_sensor_val < 2500:
            self.motor_speed = 50
            self.omega = 22
        elif 2500 <= max_sensor_val:
            self.motor_speed = 30
            self.omega = 13

    async def is_kidnap(self):
        # print(list(self.node['prox.ground.reflected']))
        await self.client.wait_for_node()
        self.reflected_sensor_vals = list(self.node['prox.ground.reflected'])
        if all([x < self.height_threshold for x in self.reflected_sensor_vals]):
            print("Kidnap")
            self.kidnap = True
        else:
            self.kidnap = False

    async def check_deadlock(self):
        if len(self.is_alter) > 20 and sum(self.is_alter) == 0: # turn right and turn left alternately over 20 times
            print(">>Deadlock")
            self.deadlock_flag = True

    async def turn_left(self):
        self.turn_direction = -1
        self.omega = -self.omega
        self.start = time.time()
        await self.node.set_variables(self.motor(-self.motor_speed, self.motor_speed))
        self.end = time.time()
        self.time = self.end - self.start
        self.is_alter.append(self.turn_direction)

    async def turn_right(self):
        self.turn_direction = 1

        self.start = time.time()
        await self.node.set_variables(self.motor(self.motor_speed, -self.motor_speed))
        self.end = time.time()
        self.time = self.end - self.start
        self.is_alter.append(self.turn_direction)

    async def forward(self, omega=0):
        self.turn_direction = 0
        await self.node.set_variables(self.motor(self.motor_speed-omega, self.motor_speed+omega))
        self.is_alter = [] # reset

    async def backward(self):
        self.turn_direction = 0
        await self.node.set_variables(self.motor(-self.motor_speed, -self.motor_speed))
        self.is_alter = [] # reset

    async def follow_global_path(self, angle):
        self.omega = -int(angle)
        self.motor_speed = 180-abs(int(3*self.omega))
        if self.motor_speed < 0:
            self.motor_speed = 0
        if self.omega>200:
            self.omega=200
        await self.forward(self.omega)

    async def stop(self):
        self.turn_direction = 0
        self.motor_speed=0
        await self.node.set_variables(self.motor(self.motor_speed, self.motor_speed))
        self.is_alter = [] # reset


    async def avoid(self, angle):
        self.update_proxsensor()
        front_prox_horizontal = self.sensor_vals[:5]
        back_prox_horizontal = self.sensor_vals[5:]

        self.print_sensor_values()

        # check whether Thymio is kidnapping
        aw(self.is_kidnap())

        # compute the proper motor speed according to the distance of obstacle
        self.compute_motor_speed()

        if all([x < self.dist_threshold for x in front_prox_horizontal]): # no obstacle
            if(self.resolvedObstacle):
                self.motor_speed=180
                await self.forward()
                await self.client.sleep(1)
            await self.follow_global_path(angle)
            self.time = 0
            self.deadlock_flag = False # free to deadlock
            self.resolvedObstacle = False
            self.is_alter = [] # reset

        if not self.deadlock_flag:
            if front_prox_horizontal[2] > self.dist_threshold: # front obstacle
                if(not self.resolvedObstacle):
                    print("Front obstacle")
                self.resolvedObstacle = True
                if front_prox_horizontal[2] > 4000: # too close to the obstacle
                    await self.backward()

                if (front_prox_horizontal[1] - front_prox_horizontal[3]) < -100: # close to right
                    await self.turn_left()
                elif (front_prox_horizontal[1] - front_prox_horizontal[3]) < 100: # close to left
                    await self.turn_right()
                else:
                    if np.random.randint(20) < 1: # probability 0.05
                        print(">> Explore!")
                        await self.turn_left()
                    else: # probability 0.95
                        await self.turn_right()

            elif any([x > self.dist_threshold for x in front_prox_horizontal[:2]]): # left obstacle
                if(not self.resolvedObstacle):
                    print("Left obstacle")
                self.resolvedObstacle = True
                await self.turn_right()

            elif any([x > self.dist_threshold for x in front_prox_horizontal[3:]]): # right obstacle
                if(not self.resolvedObstacle):
                    print("Right obstacle")
                self.resolvedObstacle = True
                await self.turn_left()

            elif any([x > self.dist_threshold for x in back_prox_horizontal]): # back obstacle
                print("Back obstacle")
                await self.forward()
                self.time = 0
        else: # in deadlock situation
            await self.turn_right() # turn right until there is no obstacle

        # compute Thymio's rotated direction (angle)
        self.compute_angle()

        # check whether Thymio stuck in deadlock
        await self.check_deadlock()

    async def run(self, angle):
        await self.avoid(angle)
        return self.motor_speed, self.omega, self.kidnap