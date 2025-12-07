# DAMP - plops down on belly, aggressive, falls down too quickly
"{id: 0, topic: 'rt/api/sport/request', api_id: 1001, parameter: '', priority: 0}"

# STOPMOVE - not sure what this does, sending it while also sending MOVE doesn't stop the robot
"{id: 0, topic: 'rt/api/sport/request', api_id: 1003, parameter: '', priority: 0}"

# STANDUP - stands up
"{id: 0, topic: 'rt/api/sport/request', api_id: 1004, parameter: '', priority: 0}"

# STANDDOWN - lies down on belly gently much softer, should be prefered
"{id: 0, topic: 'rt/api/sport/request', api_id: 1005, parameter: '', priority: 0}"

# RECOVERYSTAND - also stands up, not sure what the difference is yet
"{id: 0, topic: 'rt/api/sport/request', api_id: 1006, parameter: '', priority: 0}"

# EULER - twists in place - X does rolls (barrel roll), Y does tilt (up/down), Z does twist (on a dime). Feet stay planted
"{id: 0, topic: 'rt/api/sport/request', api_id: 1007, parameter: '{\"x\": 0.1, \"y\": 0, \"z\": 0}', priority: 1}"

# MOVE - velocity move. X is forward/backward, Y is strafing (left/right), Z is turning (on a dime). Send continuously at 10Hz+
"{id: 0, topic: 'rt/api/sport/request', api_id: 1008, parameter: '{\"x\": -0.1, \"y\": 0, \"z\": 0}', priority: 1}"

# SIT - sits down with hind legs, front legs still standing, like a dog
"{id: 0, topic: 'rt/api/sport/request', api_id: 1009, parameter: '', priority: 0}"

# RISESIT - gets up from SIT, DONT USE THIS WHILE NOT SITTING!
"{id: 0, topic: 'rt/api/sport/request', api_id: 1010, parameter: '', priority: 0}"

# SPEEDLEVEL - no idea what this does
"{id: 0, topic: 'rt/api/sport/request', api_id: 1015, parameter: '{\"data\": 0}', priority: 0}"

# HELLO - waves one of the front legs
"{id: 0, topic: 'rt/api/sport/request', api_id: 1016, parameter: '', priority: 0}"

# STRETCH - Puts front legs forward, leans down, leans back up, hind legs down, leans back, etc
"{id: 0, topic: 'rt/api/sport/request', api_id: 1017, parameter: '', priority: 0}"

# CONTENT - unknown, not implemented in v1.1.1
"{id: 0, topic: 'rt/api/sport/request', api_id: 1020, parameter: '', priority: 0}"

# DANCE1 - fixed routine, not sure how to cancel. does some jumping left and right on the spot
"{id: 0, topic: 'rt/api/sport/request', api_id: 1022, parameter: '', priority: 0}"

# DANCE2 - similar to DANCE1, but longer
"{id: 0, topic: 'rt/api/sport/request', api_id: 1023, parameter: '', priority: 0}"

# SWITCHJOYSTICK - unknonw, not implemented in v1.1.1
"{id: 0, topic: 'rt/api/sport/request', api_id: 1027, parameter: '{\"flag\": false}', priority: 0}"

# POSE - unknonw, not implemented in v1.1.1
"{id: 0, topic: 'rt/api/sport/request', api_id: 1028, parameter: '{\"flag\": false}', priority: 0}"

# FRONTJUMP - crouches and jumps forward, about 1.5 robot lengths
"{id: 0, topic: 'rt/api/sport/request', api_id: 1031, parameter: '', priority: 0}"

# FRONTPOUNCE - crouches slightly and pretends to jump forward, but doesn't move forward much
"{id: 0, topic: 'rt/api/sport/request', api_id: 1032, parameter: '', priority: 0}"

# STATICWALK - crouches and jumps forward, about 1.5 robot lengths
"{id: 0, topic: 'rt/api/sport/request', api_id: 1061, parameter: '', priority: 0}"

# HANDSTAND - unknown, not implemented in v1.1.1
"{id: 0, topic: 'rt/api/sport/request', api_id: 2044, parameter: '{\"flag\": false}', priority: 0}"
"{id: 0, topic: 'rt/api/sport/request', api_id: 2044, parameter: '{\"flag\": true}', priority: 0}"

# STATICWALK - crouches and jumps forward, about 1.5 robot lengths
"{id: 0, topic: 'rt/api/sport/request', api_id: 2045, parameter: '', priority: 0}"


---


# OBSTACLE_AVOID SWITCH_SET
"{id: 0, topic: 'rt/api/obstacles_avoid/request', api_id: 1001, parameter: '{\"enable\": true}', priority: 0}"


# BALANCE_STAND
"{id: 0, topic: 'rt/api/sport/request', api_id: 1002, parameter: '', priority: 0}"


make each one a function call. take the id, priority and any values in the parameter fields as args to functions. The topic and api_id are specific to each function. 
