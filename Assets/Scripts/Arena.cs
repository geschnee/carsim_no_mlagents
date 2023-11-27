using UnityEngine;
using System.Collections;
using System.Collections.Generic;

using System.Text;

using System.IO;
using System.Linq;

public class Arena : MonoBehaviour
{
    // TODO in theory this class could replace the GameManager class in it's responsibilities

    EpisodeManager episodeManager;

    public GameObject gameManagerObject;

    GameManager gameManager;

    GameObject car;
    Camera carCam;

    AIEngine aIEngine;

    private float rewardAsync = 0f;

    private int instancenumber;

    // from CarAgent.cs width was 512 and height was 256
    // we reduce the size to make it easier for the python code to handle the images
    // so more fit in the replay buffer
    int resWidth = 168;
    int resHeight = 168;
    // resolution is quite high: https://www.raspberrypi.com/documentation/accessories/camera.html


    void Awake()
    {
        // initialize new arena at the correct position

        // initialize the private variables

        this.gameManager = gameManagerObject.GetComponent<GameManager>();

    }

    public void setInstanceNumber(int instancenumber)
    {
        this.instancenumber = instancenumber;
    }

    public void destroyMap()
    {
        if (this.car != null)
        {
            Debug.Log($"will destroy existing car");
            Destroy(this.car);
        }

        // destroy previous obstacles:
        gameManager.DestroyObstaclesOnMap();
    }

    public string reset(MapType mt, bool jetBotSpawnpointRandom, bool singleGoalTraining)
    {
        if (this.car != null)
        {
            Debug.Log($"will destroy existing car");
            this.car.SetActive(false);
            // else there was strange behaviour when the new objects were spawned
            // it looked like there was collision detection for the Destroyed car
            Destroy(this.car);
        }

        // destroy previous obstacles:
        gameManager.DestroyObstaclesOnMap();

        Debug.Log($"startEpisode");

        // spawn new obstacles:
        MapData md = gameManager.InitializeMapWithObstacles(mt, 0, jetBotSpawnpointRandom, singleGoalTraining);


        GameObject car = gameManager.spawnJetbot(md);

        this.car = car;

        this.aIEngine = car.GetComponent<AIEngine>();
        this.carCam = car.GetComponentInChildren<Camera>();


        episodeManager = car.GetComponent<EpisodeManager>();
        episodeManager.StartEpisode();

        return GetCameraInput();
    }

    public void forwardInputsToCar(float inputAccelerationLeft, float inputAccelerationRight)
    {
        //Debug.Log($"forward left {inputAccelerationLeft} right {inputAccelerationRight}");
        aIEngine.SetInput(inputAccelerationLeft, inputAccelerationRight);

    }

    public StepReturnObject immediateStep(float inputAccelerationLeft, float inputAccelerationRight)
    {
        // TODO maybe move this code to the episodeManager
        aIEngine.SetInput(inputAccelerationLeft, inputAccelerationRight);
        episodeManager.IncreaseSteps();

        float reward = episodeManager.GetReward();
        bool done = episodeManager.IsTerminated();
        bool terminated = episodeManager.IsTerminated();
        string observation = GetCameraInput();



        Dictionary<string, string> info = episodeManager.GetInfo();

        List<float> bootstrapped_rewards = episodeManager.GetBootstrappedRewards();

        return new StepReturnObject(observation, reward, done, terminated, info, bootstrapped_rewards);
    }

    public void asyncStepPart1(float inputAccelerationLeft, float inputAccelerationRight)
    {
        aIEngine.SetInput(inputAccelerationLeft, inputAccelerationRight);
        episodeManager.IncreaseSteps();

        rewardAsync = episodeManager.rewardSinceLastGetReward;
        // part1 sets the actions, python does the waiting, then part2 returns the observation
    }

    public StepReturnObject asyncStepPart2()
    {
        float new_reward = episodeManager.GetReward();

        float reward_during_waiting = new_reward - rewardAsync;

        Debug.Log($"reward during waiting: {reward_during_waiting}");

        bool done = episodeManager.IsTerminated();
        bool terminated = episodeManager.IsTerminated();
        string observation = GetCameraInput();


        Dictionary<string, string> info = episodeManager.GetInfo();


        List<float> bootstrapped_rewards = episodeManager.GetBootstrappedRewards();

        return new StepReturnObject(observation, reward_during_waiting, done, terminated, info, bootstrapped_rewards);
    }


    //Get the AI vehicles camera input encode as byte array
    private string GetCameraInput()
    {
        // TODO should the downsampling to 84 x 84 happen here instead of python?
        RenderTexture rt = new RenderTexture(this.resWidth, this.resHeight, 24);
        carCam.targetTexture = rt;
        Texture2D screenShot = new Texture2D(this.resWidth, this.resHeight, TextureFormat.RGB24, false);
        carCam.Render();
        RenderTexture.active = rt;
        screenShot.ReadPixels(new Rect(0, 0, resWidth, resHeight), 0, 0);

        System.Byte[] pictureInBytes = screenShot.EncodeToPNG(); // the length of the array changes depending on the content
                                                                 // screenShot.EncodeToJPG(); auch möglich

        carCam.targetTexture = null;
        RenderTexture.active = null; // JC: added to avoid errors
        Destroy(rt);
        Destroy(screenShot);

        File.WriteAllBytes("observation.png", pictureInBytes);



        string base64_string = System.Convert.ToBase64String(pictureInBytes);


        byte[] base64EncodedBytes = System.Convert.FromBase64String(base64_string);

        /*
        Debug.Log($"base64_string {base64_string}");

        Debug.Log($"Shape of byte[] {pictureInBytes.Length}");
        Debug.Log($"byte[] {pictureInBytes}");
        Debug.Log($"first byte: {pictureInBytes[0]}");

        Debug.Log($"base64EncodedBytes {base64EncodedBytes}");
        Debug.Log($"base64EncodedBytes length {base64EncodedBytes.Length}");
        Debug.Log($"base64EncodedBytes first char {base64EncodedBytes[0]}");
        */

        return base64_string;
    }

    public string getObservation()
    {
        if (car == null)
        {
            // car is not spawned yet, give some default image
            return DefaultImage.getDefaultImage();
        }

        //Debug.Log("getObservation");
        string cameraPicture = GetCameraInput();
        return cameraPicture;
    }
}