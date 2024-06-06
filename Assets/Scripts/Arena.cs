using UnityEngine;
using System.Collections;
using System.Collections.Generic;

using System.Text;

using System.IO;
using System.Linq;

public class Arena : MonoBehaviour
{

    EpisodeManager episodeManager;

    public GameObject mapManagerObject;

    MapManager mapManager;

    GameObject car;
    Camera carCam;

    AIEngineBase aIEngine;

    private int instancenumber;

    // from CarAgent.cs width was 512 and height was 256
    // we reduce the size to make it easier for the python code to handle the images
    // so more fit in the replay buffer
    // these parameters are set in python config now, we want to be able to control the resolution and width/height ratio
    public int resWidth; // = 336;
    public int resHeight; // = 168;
    // resolution is quite high: https://www.raspberrypi.com/documentation/accessories/camera.html

    public Camera arenaCam;
    public int arenaResWidth = 512;
    public int arenaResHeight = 512;

    public List<Light> lights;

    public float velocityCoefficient;
    public float orientationCoefficient;
    public float distanceCoefficient;
    public float eventCoefficient;

    public bool fixedTimesteps;
    public float fixedTimestepsLength;

    public VideoRecorder arenaRecorder;
    public VideoRecorder topViewRecorder;

    public Material skyboxMaterialBright;
    public Material skyboxMaterialDark;
    public Material skyboxMaterialDefault;

    public CollisionMode collisionMode;

    void Awake()
    {
        // initialize new arena at the correct position

        // initialize the private variables

        this.mapManager = mapManagerObject.GetComponent<MapManager>();

    }

    public void setInstanceNumber(int instancenumber)
    {
        this.instancenumber = instancenumber;
    }

    public void setCollisionMode(CollisionMode collisionMode)
    {
        this.collisionMode = collisionMode;
    }

    public void destroyMap()
    {
        if (this.car != null)
        {
            Destroy(this.car);
        }

        // destroy previous obstacles:
        mapManager.DestroyMap();
    }

    public string reset(MapType mt, float spawn_rot, LightSetting lightSetting, bool evalMode, string video_filename, string jetbot_name)
    {
        if (this.car != null)
        {
            this.car.SetActive(false);
            // else there was strange behaviour when the new objects were spawned
            // it looked like there was collision detection for the Destroyed car
            Destroy(this.car);
        }

        // destroy previous obstacles:
        mapManager.DestroyMap();

        // spawn new obstacles:
        MapData md = mapManager.InitializeMapWithObstacles(mt, spawn_rot);

        GameObject car = mapManager.SpawnJetBot(md, this.instancenumber, jetbot_name);


        this.car = car;


        this.aIEngine = car.GetComponent<AIEngineBase>();
        this.aIEngine.ResetMotor();

        this.carCam = car.GetComponentInChildren<Camera>();


        SetLightSetting(lightSetting);
        episodeManager = car.GetComponent<EpisodeManager>();

        episodeManager.velocityCoefficient = this.velocityCoefficient;
        episodeManager.orientationCoefficient = this.orientationCoefficient;
        episodeManager.distanceCoefficient = this.distanceCoefficient;
        episodeManager.eventCoefficient = this.eventCoefficient;

        episodeManager.fixedTimesteps = fixedTimesteps;
        episodeManager.fixedTimestepsLength = fixedTimestepsLength;

        episodeManager.StartEpisode(evalMode, collisionMode, mt, jetbot_name);
        episodeManager.arenaRecorder = arenaRecorder;
        episodeManager.topViewRecorder = topViewRecorder;


        VideoRecorder jetBotRecorder = car.GetComponent<VideoRecorder>();
        episodeManager.jetBotRecorder = jetBotRecorder;


        if (video_filename != "")
        {
            arenaRecorder.episodeManager = episodeManager;
            arenaRecorder.StartVideo(video_filename);

            jetBotRecorder.episodeManager = episodeManager;
            jetBotRecorder.StartVideo(video_filename + "_jetbot");

            topViewRecorder.episodeManager = episodeManager;
            topViewRecorder.StartVideo(video_filename + "_topview");
        }

        return this.getObservation();
    }

    public void SetLightSetting(LightSetting lightSetting)
    {
        float lightMultiplier;
        if (lightSetting == LightSetting.bright)
        {
            lightMultiplier = 7.5f;
        }
        else if (lightSetting == LightSetting.standard)
        {
            lightMultiplier = 5f;
        }
        else if (lightSetting == LightSetting.dark)
        {
            lightMultiplier = 2.5f;
        }
        else
        {

            lightMultiplier = -100;
            Debug.LogError("LightSetting random should not be used");
        }

        foreach (Light light in lights)
        {
            light.intensity = lightMultiplier;
        }

        // This sets the skybox material of the agent's camera based on the lightMultiplier
        Skybox skybox = carCam.GetComponent<Skybox>();

        if (lightMultiplier < 4)
        {
            skybox.material = skyboxMaterialDark;
        }
        else if (lightMultiplier > 6)
        {
            skybox.material = skyboxMaterialBright;
        }
        else
        {
            skybox.material = skyboxMaterialDefault;
        }
    }

    public void forwardInputsToCar(float inputAccelerationLeft, float inputAccelerationRight)
    {
        aIEngine.SetInput(inputAccelerationLeft, inputAccelerationRight);
    }

    public EpisodeManager getEpisodeManager()
    {
        return episodeManager;
    }

    public StepReturnObject step(int step, float inputAccelerationLeft, float inputAccelerationRight)
    {

        if (episodeManager.fixedTimesteps && episodeManager.IsTerminated() == false)
        {
            if (episodeManager.episodeStatus != EpisodeStatus.WaitingForStep)
            {
                return new StepReturnObject(previousStepNotFinished: true);
            }
        }


        // when the error happens is the other input the same?
        aIEngine.SetInput(inputAccelerationLeft, inputAccelerationRight);
        episodeManager.IncreaseSteps(step);


        bool done = episodeManager.IsTerminated();
        bool terminated = episodeManager.IsTerminated();
        string observation = this.getObservation();


        Dictionary<string, string> info = episodeManager.GetInfo();
        List<float> rewards = episodeManager.GetRewards();

        return new StepReturnObject(observation, done, terminated, info, rewards);
    }


    //Get the AI vehicles camera input encode as byte array
    private string GetCameraInput(Camera cam, int resWidth, int resHeight, string filename)
    {
        // TODO should the downsampling to 84 x 84 happen here instead of python?
        // yes
        RenderTexture rt = new RenderTexture(resWidth, resHeight, 24);
        cam.targetTexture = rt;
        Texture2D screenShot = new Texture2D(resWidth, resHeight, TextureFormat.RGB24, false);
        cam.Render();
        RenderTexture.active = rt;
        screenShot.ReadPixels(new Rect(0, 0, resWidth, resHeight), 0, 0);

        System.Byte[] pictureInBytes = screenShot.EncodeToPNG(); // the length of the array changes depending on the content
                                                                 // screenShot.EncodeToJPG(); auch möglich

        cam.targetTexture = null;
        RenderTexture.active = null; // JC: added to avoid errors
        Destroy(rt);
        Destroy(screenShot);

        //Debug.Log($"Writing file {filename} with length {pictureInBytes.Length}");
        //File.WriteAllBytes(filename, pictureInBytes);

        string base64_string = System.Convert.ToBase64String(pictureInBytes);




        /*
        byte[] base64EncodedBytes = System.Convert.FromBase64String(base64_string);

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

    //Get the AI vehicles camera input encode as byte array
    private System.Byte[] GetCameraInputBytes(Camera cam, int resWidth, int resHeight, string filename)
    {
        // TODO should the downsampling to 84 x 84 happen here instead of python?
        // yes
        RenderTexture rt = new RenderTexture(resWidth, resHeight, 24);
        cam.targetTexture = rt;
        Texture2D screenShot = new Texture2D(resWidth, resHeight, TextureFormat.RGB24, false);
        cam.Render();
        RenderTexture.active = rt;
        screenShot.ReadPixels(new Rect(0, 0, resWidth, resHeight), 0, 0);

        System.Byte[] pictureInBytes = screenShot.EncodeToPNG(); // the length of the array changes depending on the content
                                                                 // screenShot.EncodeToJPG(); auch möglich

        cam.targetTexture = null;
        RenderTexture.active = null; // JC: added to avoid errors
        Destroy(rt);
        Destroy(screenShot);

        //Debug.Log($"Writing file {filename} with length {pictureInBytes.Length}");
        //File.WriteAllBytes(filename, pictureInBytes);

        return pictureInBytes;
    }

    public string getObservation()
    {
        if (car == null)
        {
            Debug.LogError("car is null");
            // car is not spawned yet, give some default image
            return DefaultImage.getDefaultImage();
        }

        //Debug.Log("getObservation");
        return GetCameraInput(this.carCam, this.resWidth, this.resHeight, "observation.png");
    }

    public System.Byte[] getObservationBytes()
    {
        if (car == null)
        {
            Debug.LogError("car is null");
            // car is not spawned yet, give some default image
            return DefaultImage.getDefaultImageBytes();
        }

        System.Byte[] pictureInBytes = GetCameraInputBytes(this.carCam, this.resWidth, this.resHeight, "observation.png");


        Debug.Log($"picture in bytes length {pictureInBytes.Length}");
        Debug.Log($"picture in bytes first 10 bytes {Encoding.Default.GetString(pictureInBytes.Take(10).ToArray())}");
        Debug.Log($"picture in bytes first 10 bytes {Encoding.UTF8.GetString(pictureInBytes.Take(10).ToArray())}");
        return pictureInBytes;
    }

    public string getArenaScreenshot()
    {
        string cameraPicture = GetCameraInput(this.arenaCam, this.arenaResWidth, this.arenaResHeight, "arena.png");
        return cameraPicture;
    }

    public string getArenaTopview()
    {
        string cameraPicture = GetCameraInput(this.topViewRecorder.cam, this.arenaResWidth, this.arenaResHeight, "topview.png");
        return cameraPicture;
    }
}