# Requirements Camera Module

A new hardware abstraction with discovery is a camera module. It has local processing that can be directed from an orchestration engine.



## Modes

As a new hardware item, it behaves like a fixture with a placeable position and view direction on the stage. One or more can be placed.
Once placed:



* static stage setup, a scan button on the layout page would use the camera to locate objects that would then be created as objects in the stage layout
* auto field of view, a setup mode on the orchestration engine side to use coloured flashing patterns to "see" where different lights have an effect, and to check the limits of the moving heads if any. LEDs would not be used. the end result would be in creating the cone patterns for each fixture and range of motion for moving heads.
* tracking mode, it can use a local engine to watch for a person, or people, and track their location on or in front of the stage and post back to the orchestration engine these stage objects that are moving. objects that are identified are currently people, but plan for future different objects. The object will be created of that "type" on the stage. Moving heads will have an action to track and point to that object type with potential offsets. this would use the temporal objects interface to create people with a unique id and update their positions



\## Software



The camera will be running on different hardware than the ESP, but should behave similarly, it should be able to be OTA updated.



Lower end camera hardware will be an OrangePI Zero with a Freenove FNK0056 camera attached for static setup and auto field of view

A higher end system with an OrangePI 4A with a USB camera for the tracking capabilities. The hardware should be able to report to the orchestration engine if it has the capability for tracking. 



Initial setup for the devices will require a user to install the available PI OS and have support for configuring a saved SSH session for future software updating, or if not required and the software can handle its own upgrading OTA that would be prefered

