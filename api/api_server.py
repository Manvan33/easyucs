import base64
import copy
import ctypes
import datetime
import json
import os
import shutil
import signal
import sys
import tarfile
import threading
import time
import uuid

from cryptography.fernet import Fernet
from functools import wraps

from flask import Flask, Response, request, send_file, after_this_request
from flask_cors import CORS
from werkzeug.exceptions import BadRequest
from werkzeug.utils import secure_filename

from common import validate_json, encrypt, decrypt, extract, guess_image_metadata
from __init__ import EASYUCS_ROOT, __version__
from device.device import GenericDevice

app = Flask(__name__)
cors = CORS(app)
easyucs = None
notifs = 0
logContent = ""
displayLogsBool = False

timeout_values = {
    "claim_to_intersight": 300,
    "clear_intersight_claim_status": 300,
    "clear_sel_logs": 300,
    "fetch_backup": 600,
    "fetch_config": 3600,
    "fetch_config_inventory": 7200,
    "fetch_inventory": 3600,
    "generate_report": 300,
    "push_config": 10800,
    "test_connection": 300
}


def timeout_wrapper(action_func):
    @wraps(action_func)
    def decorated(timeout=900, **kwargs):
        obj = kwargs.get("device", None)
        action_type = kwargs.get("action_type", None)
        object_type = kwargs.get("object_type", None)
        task_uuid = kwargs.get("task_uuid", None)

        if obj is None:
            easyucs.logger(level="error", message="No device provided")
            sys.exit()

        if task_uuid is None:
            easyucs.logger(level="error", message="No task UUID provided")
            sys.exit()

        obj.task = easyucs.task_manager.find_task_by_uuid(uuid=task_uuid)
        if obj.task is None:
            easyucs.logger(level="error", message="Could not find task UUID")
            sys.exit()

        # We start the timer
        start_timer = time.time()

        # We start the task
        easyucs.task_manager.start_task(uuid=task_uuid)

        # We perform the main action on a separate thread
        action_thread = threading.Thread(target=action_func, kwargs=kwargs,
                                         name=action_type + "_" + object_type + "_" + str(task_uuid))
        action_thread.start()

        easyucs.logger(
            message=f"Waiting up to {str(timeout)} seconds for the {str(action_type)} {str(object_type)} "
                    f"operation to be performed on the {obj.metadata.device_type_long} device"
        )
        thread_cancelled = False
        i = 0
        # We wait for the thread to end or to be cancelled by user
        while (time.time() - start_timer) < timeout:
            time_left = int(timeout - (time.time() - start_timer))
            if not action_thread.is_alive():
                easyucs.logger(level="debug",
                               message="Thread for the " + str(action_type) + " " + str(object_type) +
                                       " operation is finished")
                break
            if obj.task.cancel:
                thread_cancelled = True
                break
            if i % 60 == 0:
                easyucs.logger(level="debug",
                               message="Waiting up to " + str(time_left) + " seconds for the " + str(action_type) +
                                       " " + str(object_type) + " operation on thread '" +
                                       str(action_thread.getName()) + "' to finish...")
            time.sleep(1)
            i += 1

        if thread_cancelled:
            easyucs.logger(level="debug",
                           message="Received signal to cancel the task. Killing thread " + str(action_thread.getName())
                                   + "...")
            terminate_thread(action_thread)

            # We stop the task
            easyucs.task_manager.stop_task(uuid=task_uuid, status="failed", status_message="Task cancelled by user")

        elif action_thread.is_alive():
            easyucs.logger(level="debug",
                           message="Timeout exceeded. Killing thread " + str(action_thread.getName()) + "...")
            terminate_thread(action_thread)

            # We stop the task
            easyucs.task_manager.stop_task(uuid=task_uuid, status="failed",
                                           status_message="Timeout of " + str(timeout) + " seconds exceeded")
        else:
            # We stop the task
            easyucs.task_manager.stop_task(uuid=task_uuid)

        # We release 1 token to the available tokens
        easyucs.task_manager.available_tokens.put(1)
        easyucs.logger(level="debug",
                       message="Released 1 token. " + str(easyucs.task_manager.available_tokens.qsize()) +
                               " token(s) left")

        # We reset the task attribute on the device
        obj.task = None

        # If the device has any queued tasks, then we move the oldest one to the system's (task manager's)
        # task queue.
        if not obj.queued_tasks.empty():
            if not easyucs.task_manager.add_to_pending_tasks(obj.queued_tasks.get()):
                # Should not happen theoretically
                easyucs.logger(level="error",
                               message="Error while scheduling the device's queued task. System's task queue "
                                       "might be full")

        sys.exit()

    return decorated


def fetch_config_inventory(device=None, force=False):
    """
    Performs config and inventory fetch sequentially
    :param device: The device for which the fetch is to be performed
    :return: True if both config and inventory are fetched successfully else False
    """
    if device is None:
        easyucs.logger(level="error", message="No device provided")
        sys.exit()

    config_uuid = device.config_manager.fetch_config(force=force)
    inventory_uuid = device.inventory_manager.fetch_inventory(force=force)
    if config_uuid and inventory_uuid:
        return True
    return False


def get_object_from_db(object_type=None, uuid=None, device_uuid=None, task_uuid=None, filter=None,
                       order_by=None, page_number=0, page_size=None):
    """
    Gets all objects' metadata from the Database
    :param object_type: Type of object to get metadata of (backup/config/device/inventory/report/task/taskstep)
    :param uuid: Only get metadata that matches the specified UUID
    :param device_uuid: Filter results to only include entries for a given device UUID
    :param task_uuid: Filter results to only include entries for a given task UUID
    :param filter: Optional array of attributes to filter in the get operation
    :param order_by: Order of returned objects (Tuple with object attribute and order [desc, asc])
    :param page_size: Number of returned objects
    :param page_number: Page number of returned objects
    :return: Dictionary with the list of objects' metadata if successful, None otherwise
    """

    if object_type not in ["backup", "config", "device", "inventory", "report", "task", "taskstep"]:
        easyucs.logger(level="error", message="Invalid object type provided")
        return None

    if uuid and device_uuid:
        easyucs.logger(level="error", message="Both uuid and device uuid are provided")
        return None

    if uuid and task_uuid:
        easyucs.logger(level="error", message="Both uuid and task uuid are provided")
        return None

    if device_uuid and task_uuid:
        easyucs.logger(level="error", message="Both device uuid and task uuid are provided")
        return None

    if filter and len(filter) != 3:
        easyucs.logger(level="error", message="Filter length is not equal to 3")
        return None

    if filter:
        if filter[0] in ["is_system", "is_hidden"]:
            if filter[2].lower() == "true":
                filter[2] = True
            elif filter[2].lower() == "false":
                filter[2] = False
        filter = (filter[0], filter[1], filter[2])

    if order_by is None:
        order_by = ("timestamp", "desc")
    object_metadata_list = []

    # If we get a single object we put the name to singular, otherwise to plural
    if uuid:
        dict_name = object_type
    else:
        if object_type == "inventory":
            dict_name = "inventories"
        elif object_type == "taskstep":
            dict_name = "steps"
        else:
            dict_name = object_type + "s"

    for object_metadata in easyucs.repository_manager.get_metadata(
            object_type=object_type, uuid=uuid, device_uuid=device_uuid, task_uuid=task_uuid, filter=filter,
            order_by=order_by, limit=page_size, page=page_number
    ):
        obj = {}

        if hasattr(object_metadata, 'device_uuid') and getattr(object_metadata, 'device_uuid'):
            obj["device_uuid"] = str(object_metadata.device_uuid)
        if hasattr(object_metadata, 'uuid'):
            obj["uuid"] = str(object_metadata.uuid)
        if hasattr(object_metadata, 'timestamp'):
            obj["timestamp"] = object_metadata.timestamp.isoformat()[:-3] + 'Z'

        # If we are not dealing with a task, these attributes are common to all the other objects
        if object_type not in ["task", "taskstep"]:
            for attribute in ["device_version", "easyucs_version", "is_hidden", "is_system", "name", "origin",
                              "system_usage"]:
                if getattr(object_metadata, attribute) not in [None, ""]:
                    obj[attribute] = getattr(object_metadata, attribute)

        if object_type == "backup":
            for attribute in ["backup_type"]:
                if getattr(object_metadata, attribute) not in [None, ""]:
                    obj[attribute] = getattr(object_metadata, attribute)

        elif object_type == "config":
            for attribute in ["category", "origin", "revision", "subcategory", "url"]:
                if getattr(object_metadata, attribute) not in [None, ""]:
                    obj[attribute] = getattr(object_metadata, attribute)

        elif object_type == "device":
            for attribute in ["bypass_version_checks", "device_connector_claim_status",
                              "device_connector_ownership_name", "device_connector_ownership_user", "device_name",
                              "device_type", "device_type_long", "is_reachable", "key_id", "target",
                              "timestamp_last_connected", "username"]:
                if getattr(object_metadata, attribute) not in [None, ""]:
                    if "timestamp" in attribute:
                        obj[attribute] = getattr(object_metadata, attribute).isoformat()[:-3] + 'Z'
                    else:
                        obj[attribute] = getattr(object_metadata, attribute)

            for attribute in ["intersight_device_uuid"]:
                if getattr(object_metadata, attribute) not in [None, ""]:
                    obj[attribute] = str(getattr(object_metadata, attribute))

        elif object_type == "inventory":
            # No specific attributes for inventories
            pass

        elif object_type == "report":
            for attribute in ["language", "output_format", "page_layout", "report_type", "size"]:
                if getattr(object_metadata, attribute) not in [None, ""]:
                    obj[attribute] = getattr(object_metadata, attribute)

        elif object_type == "task":
            for attribute in ["config_uuid", "description", "device_name", "easyucs_version", "inventory_uuid", "name",
                              "progress", "report_uuid", "status", "status_message", "target_device_uuid",
                              "timestamp_start", "timestamp_stop"]:
                if getattr(object_metadata, attribute) not in [None, ""]:
                    if "timestamp" in attribute:
                        obj[attribute] = getattr(object_metadata, attribute).isoformat()[:-3] + 'Z'
                    elif "uuid" in attribute:
                        obj[attribute] = str(getattr(object_metadata, attribute))
                    else:
                        obj[attribute] = getattr(object_metadata, attribute)

        elif object_type == "taskstep":
            for attribute in ["description", "name", "optional", "order", "status", "status_message", "timestamp_start",
                              "timestamp_stop", "weight"]:
                if getattr(object_metadata, attribute) not in [None, ""]:
                    if "timestamp" in attribute:
                        obj[attribute] = getattr(object_metadata, attribute).isoformat()[:-3] + 'Z'
                    else:
                        obj[attribute] = getattr(object_metadata, attribute)

        else:
            return None

        object_metadata_list.append(obj)

    # If we take a single object, we do not return a list but the object directly
    if uuid:
        if len(object_metadata_list) == 1:
            object_dict = {dict_name: object_metadata_list[0]}
        else:
            easyucs.logger(level="error", message=f"Could not find {object_type} with uuid {str(uuid)}")
            return None
    else:
        object_dict = {dict_name: object_metadata_list}
    return object_dict


def load_object(object_type=None, object_uuid=None, device=None):
    """
    Loads an object in the corresponding object list in memory
    :param object_type: Type of object to be loaded (config/device/inventory)
    :param object_uuid: The UUID of the object to be loaded
    :param device: The device to which the object belongs (optional)
    :return: Object loaded in memory if successful, None otherwise
    """

    if object_type not in ["config", "device", "inventory", "report"]:
        easyucs.logger(level="error", message="Invalid object type provided")
        return None

    if not object_uuid:
        easyucs.logger(level="error", message="Missing UUID of object")
        return None

    if object_type == "device":
        manager_target = getattr(easyucs, object_type + "_manager")
    else:
        if not device:
            easyucs.logger(level="error", message="Missing device for which object must be loaded")
            return None
        manager_target = getattr(device, object_type + "_manager")

    obj = getattr(manager_target, "find_" + object_type + "_by_uuid")(uuid=object_uuid)

    if not obj:
        # Object may not have been loaded into object list yet. Trying to load it using its metadata (DB)
        easyucs.logger(level="debug",
                       message="Trying to load " + object_type + " with UUID " + object_uuid + " from the DB")
        obj_metadata_list = easyucs.repository_manager.get_metadata(object_type=object_type, uuid=object_uuid)
        if len(obj_metadata_list) == 1:
            if object_type == "device":
                result = getattr(manager_target, "add_" + object_type)(metadata=obj_metadata_list[0])
            else:
                result = getattr(manager_target, "import_" + object_type)(metadata=obj_metadata_list[0])

            if result:
                obj = getattr(manager_target, "find_" + object_type + "_by_uuid")(uuid=object_uuid)
            else:
                easyucs.logger(level="error", message="Could not perform add/import action",
                               set_api_error_message=False)
                return None

        else:
            easyucs.logger(level="error", message="Could not find " + object_type + " with UUID " + object_uuid + ": " +
                                                  str(len(obj_metadata_list)) + " objects found")
            return None

    return obj


@timeout_wrapper
def perform_action(device=None, action_type="", object_type="", task_uuid=None, action_kwargs=None):
    """
    Performs an action with an object type on a given device
    :param device: The device for which the action is to be performed
    :param action_type: The action to be performed (fetch/push/generate)
    :param object_type: The type of object to be used for this action (backup/config/inventory/report/config_inventory)
    :param task_uuid: The UUID of the associated task for this action
    :param action_kwargs: A dictionary of keyword args to pass to the action to be performed
    :return: True if successful, False otherwise
    """
    if not device:
        easyucs.logger(level="error", message="No device provided")
        sys.exit()

    if action_type not in ["claim_to_intersight", "clear_intersight_claim_status", "clear_sel_logs", "fetch",
                           "generate", "push", "test_connection"]:
        easyucs.logger(level="error", message="Invalid action type provided")
        sys.exit()

    if object_type not in ["backup", "config", "device", "inventory", "report", "config_inventory"]:
        easyucs.logger(level="error", message="Invalid object type provided")
        sys.exit()

    if not task_uuid:
        easyucs.logger(level="error", message="No task_uuid provided")
        sys.exit()

    if action_kwargs is None:
        action_kwargs = {}

    # In case this is a clear, fetch or push operation, we first need to connect to the device
    if action_type in ["clear_intersight_claim_status", "clear_sel_logs", "fetch", "push", "test_connection"]:
        if not device.connect(bypass_version_checks=device.metadata.bypass_version_checks):
            easyucs.logger(level="error",
                           message="Failed to connect to " + device.metadata.device_type_long + " device",
                           set_api_error_message=False)
            status_message = easyucs.api_error_message
            if not status_message:
                status_message = "Failed to connect to " + device.metadata.device_type_long + " device"
            easyucs.task_manager.stop_task(uuid=task_uuid, status="failed", status_message=status_message)

        # We save device metadata in case they have changed (version, name, is_reachable and timestamp_last_connected)
        easyucs.repository_manager.save_metadata(metadata=device.metadata)

        if not device.metadata.is_reachable:
            sys.exit()

    # In case this is a "generate report" operation, we first need to draw the inventory/config plots & save images
    elif action_type in ["generate"]:
        if not device.inventory_manager.draw_inventory(uuid=action_kwargs["inventory"].uuid):
            message_str = "Impossible to draw the inventory with UUID " + str(action_kwargs["inventory"].uuid) + \
                          " of " + device.metadata.device_type_long + " device"
            easyucs.logger(level="error", message=message_str)
            easyucs.task_manager.stop_task(uuid=task_uuid, status="failed", status_message=message_str)
            sys.exit()

        if not easyucs.repository_manager.save_images_to_repository(inventory=action_kwargs["inventory"]):
            message_str = "Impossible to save the images to the repository for inventory with UUID " + \
                          str(action_kwargs["inventory"].uuid) + " of " + device.metadata.device_type_long + " device"
            easyucs.logger(level="error", message=message_str)
            easyucs.task_manager.stop_task(uuid=task_uuid, status="failed", status_message=message_str)
            sys.exit()

        if device.metadata.device_type in ["ucsm"]:
            if not device.config_manager.generate_config_plots(config=action_kwargs["config"]):
                message_str = "Impossible to draw the plots for config with UUID " + \
                              str(action_kwargs["config"].uuid) + " of " + device.metadata.device_type_long + " device"
                easyucs.logger(level="error", message=message_str)
                easyucs.task_manager.stop_task(uuid=task_uuid, status="failed", status_message=message_str)
                sys.exit()

            if not easyucs.repository_manager.save_images_to_repository(config=action_kwargs["config"]):
                message_str = "Impossible to save the plot images to the repository for config with UUID " + \
                              str(action_kwargs["config"].uuid) + " of " + device.metadata.device_type_long + " device"
                easyucs.logger(level="error", message=message_str)
                easyucs.task_manager.stop_task(uuid=task_uuid, status="failed", status_message=message_str)
                sys.exit()

        # We set the directory for the pictures to be the device metadata image_path folder
        action_kwargs["directory"] = device.metadata.images_path

    # In case this is a claim to intersight operation, we first need to connect to the devices
    elif action_type in ["claim_to_intersight"]:

        if not device.connect(bypass_version_checks=device.metadata.bypass_version_checks):
            easyucs.logger(level="error",
                           message="Failed to connect to " + device.metadata.device_type_long + " device")
            status_message = easyucs.api_error_message
            if not status_message:
                status_message = "Failed to connect to " + device.metadata.device_type_long + " device"
            easyucs.task_manager.stop_task(uuid=task_uuid, status="failed", status_message=status_message)
            sys.exit()

        # Since we were able to connect, we save the device metadata in case they have changed (version & name)
        easyucs.repository_manager.save_metadata(metadata=device.metadata)

        intersight_device = action_kwargs["intersight_device"]
        # We need to add this task step to the UCS device as the 'connect()' function will only add it to
        # the Intersight device by default
        device.task.taskstep_manager.start_taskstep(
            name="ConnectIntersightDevice",
            description="Connecting to " + intersight_device.metadata.device_type_long + " device")
        if not intersight_device.connect(bypass_version_checks=device.metadata.bypass_version_checks):
            easyucs.logger(level="error",
                           message="Failed to connect to " + intersight_device.metadata.device_type_long +
                                   " device")
            status_message = easyucs.api_error_message
            if not status_message:
                status_message = "Failed to connect to " + intersight_device.metadata.device_type_long + " device"
            device.task.taskstep_manager.stop_taskstep(name="ConnectIntersightDevice", status="failed",
                                                       status_message=status_message)
            easyucs.task_manager.stop_task(uuid=task_uuid, status="failed", status_message=status_message)
            sys.exit()

        # We were able to connect, we save the Intersight device metadata in case they have changed (version & name)
        easyucs.repository_manager.save_metadata(metadata=intersight_device.metadata)
        if device.task is not None:
            device.task.taskstep_manager.stop_taskstep(
                name="ConnectIntersightDevice", status="successful",
                status_message="Successfully connected to " + intersight_device.metadata.device_type_long + " device " +
                               str(intersight_device.name))

    manager_target = None
    if object_type in ["device"] and action_type in ["claim_to_intersight", "clear_intersight_claim_status",
                                                     "clear_sel_logs"]:
        # The operation to perform is a direct call to the function at the device level
        action_target = getattr(device, action_type)

    elif object_type == "config_inventory":
        action_target = fetch_config_inventory
        if action_kwargs:
            action_kwargs["device"] = device
        else:
            action_kwargs = {
                "device": device
            }

    else:
        manager_target = getattr(device, object_type + "_manager", None)
        action_target = getattr(manager_target, action_type + "_" + object_type, None)

    response = None
    if action_target:
        response = action_target(**action_kwargs)

    # In case this is a claim, a clear, a fetch or a push operation, we now need to disconnect from the device
    if action_type in ["claim_to_intersight", "clear_intersight_claim_status", "clear_sel_logs", "fetch", "push",
                       "test_connection"]:
        device.disconnect()

    # In case this is a claim to Intersight operation, we now need to disconnect from the Intersight device
    if action_type in ["claim_to_intersight"]:
        intersight_device = action_kwargs["intersight_device"]
        # We need to add this taskstep to the UCS device as the disconnect() function will only add it to
        # the Intersight device by default
        device.task.taskstep_manager.skip_taskstep(
            name="DisconnectIntersightDevice",
            status_message="Disconnecting from " + intersight_device.metadata.device_type_long + " device " +
                           str(intersight_device.name))
        intersight_device.disconnect()

    # In case this is a push operation, we now need to generate the push summary report
    push_summary = False
    if action_type in ["push"]:
        config = device.config_manager.find_config_by_uuid(uuid=action_kwargs["uuid"])
        push_summary = device.report_manager.generate_report(config=config, output_formats=["json"],
                                                             report_type="push_summary")

    # We save the object(s) when we get successful response from action_target and none of the tasksteps have failed.
    if response and not easyucs.task_manager.is_any_taskstep_failed(uuid=task_uuid):
        if action_type in ["fetch", "generate"]:
            # We save the newly added object to the repository
            if object_type in ["config_inventory"]:
                # Saving config in repository
                obj = getattr(device.config_manager, "get_latest_config")()
                if obj is not None:
                    easyucs.repository_manager.save_to_repository(object=obj)
                # Saving inventory in repository
                obj = getattr(device.inventory_manager, "get_latest_inventory")()
                if obj is not None:
                    easyucs.repository_manager.save_to_repository(object=obj)
            elif object_type in ["report"]:
                # Determining the number of reports that have been generated
                number_of_reports = 1
                if "output_formats" in action_kwargs:
                    number_of_reports = len(list(set(action_kwargs["output_formats"])))
                if device.report_manager.report_list:
                    for report in [device.report_manager.report_list[-x] for x in range(1, number_of_reports + 1)]:
                        easyucs.repository_manager.save_to_repository(object=report)
            else:
                obj = getattr(manager_target, "get_latest_" + object_type)()
                if obj is not None:
                    easyucs.repository_manager.save_to_repository(object=obj)

        elif action_type in ["push"] and push_summary:
            # Determining the number of reports that have been generated
            number_of_reports = 1
            if "output_formats" in action_kwargs:
                number_of_reports = len(list(set(action_kwargs["output_formats"])))
            if device.report_manager.report_list:
                for report in [device.report_manager.report_list[-x] for x in range(1, number_of_reports + 1)]:
                    easyucs.repository_manager.save_to_repository(object=report)

    sys.exit()


def response_handle(response=None, code=400, mimetype="application/json"):
    """
    Function to create response objects based on the "response" and "response code".
    :param response: (str, dict, list) Response to be sent
    :param code: Response Code
    :param mimetype: Mimetype of the response being sent
    :return: Response object.
    """
    response_code_mapping = {
        200: "OK",
        400: "Invalid Request",
        404: "Resource Not Found",
        500: "Internal Server Error"
    }
    if response is not None:
        if response.__class__.__name__ == "str":
            dict_response = {"message": response}
        elif response.__class__.__name__ in ["dict", "list"]:
            dict_response = response
        else:
            dict_response = {"message": str(response)}
    else:
        dict_response = {"message": response_code_mapping.get(code, "Unexpected Error Code")}

    json_response = json.dumps(dict_response, indent=4)
    final_response = Response(response=json_response, status=code, mimetype=mimetype)
    return final_response


def terminate_task_scheduler(sig, frame):
    easyucs.logger(level="error", message="Received interrupt signal - Stopping task scheduler thread '" +
                                          str(easyucs.task_manager.scheduler_thread.getName()) + "'")
    terminate_thread(easyucs.task_manager.scheduler_thread)
    sys.exit()


signal.signal(signal.SIGINT, terminate_task_scheduler)


def terminate_thread(thread):
    """
    Terminates a Python thread from another thread
    :param thread: a threading.Thread instance
    :return: True if successfully terminated the thread, False otherwise
    """
    # https://stackoverflow.com/questions/52631315/python-properly-kill-exit-futures-thread
    if not thread.is_alive():
        return

    exc = ctypes.py_object(SystemExit)
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(thread.ident), exc)
    if res == 0:
        easyucs.logger(level="error", message=f"Unable to terminate the thread {thread.getName()}. " +
                                              f"Non existent thread id")
        return False
    elif res > 1:
        # """if it returns a number greater than one, you're in trouble,
        # and you should call it again with exc=NULL to revert the effect"""
        ctypes.pythonapi.PyThreadState_SetAsyncExc(thread.ident, None)
        easyucs.logger(level="error", message=f"Unable to terminate the thread {thread.getName()}. " +
                                              f"PyThreadState_SetAsyncExc failed")
        return False
    return True


@app.route("/")
def hello():
    return "EasyUCS API"


@app.route("/backups", methods=['GET'])
# @cross_origin()
def backups():
    if request.method == 'GET':
        try:
            filter_attribute = request.args.get("filter_attribute", None)
            filter_type = request.args.get("filter_type", None)
            filter_value = request.args.get("filter_value", None)

            if filter_attribute is None or filter_type is None or filter_value is None:
                filter = None
            else:
                filter = [filter_attribute, filter_type, filter_value]

            order_by_attribute = request.args.get("order_by_attribute", None)
            order_by_direction = request.args.get("order_by_direction", None)

            if order_by_attribute is None or order_by_direction is None:
                order_by = None
            else:
                order_by = (order_by_attribute, order_by_direction)

            page_size = request.args.get("page_size", None)
            page_number = int(request.args.get("page_number", 0))

            backup_dict = get_object_from_db(object_type="backup", filter=filter, page_size=page_size,
                                             page_number=page_number, order_by=order_by)

            if backup_dict:
                response = response_handle(backup_dict, 200)
            else:
                response = response_handle(code=500,
                                           response="Unexpected error while fetching data from DB. Please check logs.")

        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/configs", methods=['GET'])
# @cross_origin()
def configs():
    if request.method == 'GET':
        try:
            filter_attribute = request.args.get("filter_attribute", None)
            filter_type = request.args.get("filter_type", None)
            filter_value = request.args.get("filter_value", None)

            if filter_attribute is None or filter_type is None or filter_value is None:
                filter = None
            else:
                filter = [filter_attribute, filter_type, filter_value]

            order_by_attribute = request.args.get("order_by_attribute", None)
            order_by_direction = request.args.get("order_by_direction", None)

            if order_by_attribute is None or order_by_direction is None:
                order_by = None
            else:
                order_by = (order_by_attribute, order_by_direction)

            page_size = request.args.get("page_size", None)
            page_number = int(request.args.get("page_number", 0))

            config_dict = get_object_from_db(object_type="config", filter=filter, page_size=page_size,
                                             page_number=page_number, order_by=order_by)

            if config_dict:
                response = response_handle(config_dict, 200)
            else:
                response = response_handle(code=500,
                                           response="Unexpected error while fetching data from DB. Please check logs.")

        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices", methods=['GET', 'POST'])
# @cross_origin()
def devices():
    if request.method == 'GET':
        try:
            filter_attribute = request.args.get("filter_attribute", None)
            filter_type = request.args.get("filter_type", None)
            filter_value = request.args.get("filter_value", None)

            if filter_attribute is None or filter_type is None or filter_value is None:
                filter = None
            else:
                filter = [filter_attribute, filter_type, filter_value]

            order_by_attribute = request.args.get("order_by_attribute", None)
            order_by_direction = request.args.get("order_by_direction", None)

            if order_by_attribute is None or order_by_direction is None:
                order_by = None
            else:
                order_by = (order_by_attribute, order_by_direction)

            page_size = request.args.get("page_size", None)
            page_number = int(request.args.get("page_number", 0))

            device_dict = get_object_from_db(object_type="device", filter=filter, page_size=page_size,
                                             page_number=page_number, order_by=order_by)

            if device_dict:
                response = response_handle(device_dict, 200)
            else:
                response = response_handle(code=500,
                                           response="Unexpected error while fetching data from DB. Please check logs.")

        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response

    if request.method == 'POST':
        try:
            payload = request.json

            # Check if payload valid
            if not validate_json(json_data=payload, schema_path="api/specs/device_post.json", logger=easyucs):
                response = response_handle(code=400, response="Invalid payload")
                return response

            # Since this is a new device, we generate a new UUID (useful in case of private key to store)
            new_device_uuid = uuid.uuid4()

            # We first need to take care of the private key if there is one given. We need to store it in the repository
            private_key_path = None
            if "private_key" in payload:
                private_key = payload["private_key"].replace('\r', '')
                private_key_path = easyucs.repository_manager.save_key_to_repository(private_key=private_key,
                                                                                     device_uuid=new_device_uuid)

            device_type = None
            if "device_type" in payload:
                device_type = payload["device_type"]
            key_id = None
            if "key_id" in payload:
                key_id = payload["key_id"]
            password = None
            if "password" in payload:
                password = payload["password"]
            target = None
            if "target" in payload:
                target = payload["target"]
            username = None
            if "username" in payload:
                username = payload["username"]
            bypass_version_checks = False
            if "bypass_version_checks" in payload:
                bypass_version_checks = payload["bypass_version_checks"]
            if easyucs.device_manager.add_device(device_type=device_type, uuid=new_device_uuid, target=target,
                                                 username=username, password=password, key_id=key_id,
                                                 private_key_path=private_key_path,
                                                 bypass_version_checks=bypass_version_checks):
                device = easyucs.device_manager.get_latest_device()
                easyucs.repository_manager.save_to_repository(object=device)
                device_metadata = {"device_uuid": str(device.uuid),
                                   "timestamp": device.metadata.timestamp.isoformat()[:-3] + 'Z'}
                for attribute in ["bypass_version_checks", "device_name", "device_type", "device_type_long",
                                  "device_version", "easyucs_version", "is_hidden", "is_system", "key_id",
                                  "system_usage", "target", "username"]:
                    if getattr(device.metadata, attribute) not in [None, ""]:
                        device_metadata[attribute] = getattr(device.metadata, attribute)
                device_dict = {"device": device_metadata}
                response = response_handle(device_dict, 200)
            else:
                response = response_handle(code=500)

        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/types", methods=['GET'])
# @cross_origin()
def device_actions():
    if request.method == 'GET':
        try:
            # Ordered by display_name
            actions = {
                "intersight": {
                    "display_name": "Intersight",
                    "available_actions": ["test_connection"]
                },
                "cimc": {
                    "display_name": "UCS IMC",
                    "available_actions": ["claim_to_intersight", "clear_intersight_claim_status", "clear_sel_logs",
                                          "clear_user_sessions", "erase_all_virtual_drives", "erase_all_flexflash",
                                          "reset", "set_all_drives_status", "test_connection"]
                },
                "ucsc": {
                    "display_name": "UCS Central",
                    "available_actions": ["clear_user_sessions", "test_connection"]
                },
                "ucsm": {
                    "display_name": "UCS System",
                    "available_actions": ["claim_to_intersight", "clear_intersight_claim_status", "clear_sel_logs",
                                          "clear_user_sessions", "decommission_all_rack_servers",
                                          "erase_all_virtual_drives", "erase_all_flexflash", "regenerate_certificate",
                                          "reset", "test_connection"]
                }
            }

            actions_dict = {"types": actions}

            response = response_handle(actions_dict, 200)

        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/actions/claim_to_intersight", methods=['POST'])
# @cross_origin()
def devices_actions_claim_to_intersight():
    if request.method == 'POST':
        try:
            payload = request.json

            # Check if payload valid
            if not validate_json(json_data=payload, schema_path="api/specs/devices_actions_claim_to_intersight.json",
                                 logger=easyucs):
                response = response_handle(code=400, response="Invalid payload")
                return response

            device_uuid_list = payload.get("device_uuids", [])
            intersight_device_uuid = payload.get("intersight_device_uuid", None)

            device_list = []

            for device_uuid in device_uuid_list:
                device = load_object(object_type="device", object_uuid=device_uuid)
                if device:
                    device_list.append(device)
                else:
                    response = response_handle(response="Device with UUID " + device_uuid + " not found", code=404)
                    return response

            intersight_device = load_object(object_type="device", object_uuid=intersight_device_uuid)
            if not intersight_device:
                response = response_handle(response="Intersight device with UUID " + intersight_device_uuid +
                                                    " not found", code=404)
                return response

            action_kwargs = {
                "intersight_device": intersight_device
            }

            try:
                # We loop through the devices and create a task for each
                task_uuid_list = []
                for device in device_list:
                    if device.metadata.device_type in ["cimc"]:
                        task_uuid = easyucs.task_manager.add_task(name="ClaimToIntersightUcsImc",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid),
                                                                  target_device_uuid=str(intersight_device.uuid))
                    elif device.metadata.device_type in ["ucsm"]:
                        task_uuid = easyucs.task_manager.add_task(name="ClaimToIntersightUcsSystem",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid),
                                                                  target_device_uuid=str(intersight_device.uuid))
                    else:
                        response = response_handle(response="Unsupported device type (" + device.metadata.device_type +
                                                            ") for device with UUID " + str(device.uuid), code=500)
                        return response
                    task_uuid_list.append(str(task_uuid))

                # We schedule the claim to Intersight actions through the scheduler
                for task_uuid in task_uuid_list:
                    pending_task = {
                        "task_uuid": task_uuid,
                        "action_type": "claim_to_intersight",
                        "object_type": "device",
                        "timeout": timeout_values["claim_to_intersight"],
                        "action_kwargs": action_kwargs
                    }
                    if not easyucs.task_manager.add_to_pending_tasks(pending_task):
                        response = response_handle(response="Error while scheduling the task. Task Queue might be Full."
                                                            " Try again after some time.", code=400)
                        return response

            except Exception as err:
                response = response_handle(response="Error while claiming device to Intersight: " + str(err), code=500)
                return response

            response = response_handle(response={"tasks": task_uuid_list}, code=200)

        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/actions/delete", methods=['POST'])
# @cross_origin()
def devices_actions_delete():
    if request.method == 'POST':
        try:
            payload = request.json

            # Check if payload valid
            if not validate_json(json_data=payload, schema_path="api/specs/devices_actions_delete.json",
                                 logger=easyucs):
                response = response_handle(code=400, response="Invalid payload")
                return response

            device_uuid_list = payload["device_uuids"]

            devices_metadata_list = []

            for device_uuid in device_uuid_list:
                device_metadata_list = easyucs.repository_manager.get_metadata(object_type="device", uuid=device_uuid)
                if len(device_metadata_list) == 1:
                    devices_metadata_list.append(device_metadata_list[0])
                else:
                    response = response_handle(response="Device with UUID " + device_uuid + " not found", code=404)
                    return response

            if easyucs.repository_manager.delete_bulk_from_repository(metadata_list=devices_metadata_list,
                                                                      delete_keys=True):
                response = response_handle(code=200)
            else:
                response = response_handle(code=500, response="Could not delete devices from repository")

        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>", methods=['GET', 'DELETE', 'PUT'])
# @cross_origin()
def device_uuid(device_uuid):
    if request.method == 'GET':
        try:
            filter_attribute = request.args.get("filter_attribute", None)
            filter_type = request.args.get("filter_type", None)
            filter_value = request.args.get("filter_value", None)

            if filter_attribute is None or filter_type is None or filter_value is None:
                filter = None
            else:
                filter = [filter_attribute, filter_type, filter_value]

            order_by_attribute = request.args.get("order_by_attribute", None)
            order_by_direction = request.args.get("order_by_direction", None)

            if order_by_attribute is None or order_by_direction is None:
                order_by = None
            else:
                order_by = (order_by_attribute, order_by_direction)

            page_size = request.args.get("page_size", None)
            page_number = int(request.args.get("page_number", 0))

            device_dict = get_object_from_db(object_type="device", uuid=device_uuid, filter=filter, page_size=page_size,
                                             page_number=page_number, order_by=order_by)

            if device_dict:
                response = response_handle(device_dict, 200)
            else:
                response = response_handle(code=400, response=f"Could not find device with UUID {device_uuid}")

        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response

    if request.method == 'DELETE':
        try:
            device_metadata_list = easyucs.repository_manager.get_metadata(object_type="device", uuid=device_uuid)
            if len(device_metadata_list) == 1:
                if easyucs.repository_manager.delete_from_repository(metadata=device_metadata_list[0],
                                                                     delete_keys=True):
                    response = response_handle(code=200)
                else:
                    response = response_handle(code=500, response="Could not delete device from repository")
            else:
                response = response_handle(response="Device with UUID " + device_uuid + " not found", code=404)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response

    if request.method == 'PUT':
        try:
            device_metadata_list = easyucs.repository_manager.get_metadata(object_type="device", uuid=device_uuid)
            if len(device_metadata_list) == 1:
                payload = request.json
                # Check if payload valid
                if not validate_json(json_data=payload, schema_path="api/specs/device_put.json", logger=easyucs):
                    response = response_handle(code=400, response="Invalid payload")
                    return response

                updating_fields = ["target", "bypass_version_checks"]
                if device_metadata_list[0].device_type == "intersight":
                    updating_fields.extend(["key_id"])
                else:
                    updating_fields.extend(["username", "password"])

                for attribute in updating_fields:
                    if attribute in payload:
                        setattr(device_metadata_list[0], attribute, payload[attribute])
                        if device_metadata_list[0].parent is not None:
                            # We also change the attributes in the object if it is linked to the metadata
                            setattr(device_metadata_list[0].parent, attribute, payload[attribute])

                # In case the private key has been changed, we need to save it to the repository again
                if device_metadata_list[0].device_type == "intersight" and "private_key" in payload:
                    private_key = payload["private_key"].replace('\r', '')
                    easyucs.repository_manager.save_key_to_repository(private_key=private_key,
                                                                      device_uuid=device_metadata_list[0].uuid)

                # Need to call add_device to make sure that handle is updated with the latest credentials
                device = easyucs.device_manager.find_device_by_uuid(uuid=device_uuid)
                if device:
                    easyucs.device_manager.remove_device(uuid=device_uuid)
                    easyucs.device_manager.add_device(metadata=device_metadata_list[0])
                else:
                    easyucs.device_manager.add_device(metadata=device_metadata_list[0])

                easyucs.repository_manager.save_metadata(metadata=device_metadata_list[0])

                device_metadata = {"device_uuid": str(device_metadata_list[0].uuid),
                                   "timestamp": device_metadata_list[0].timestamp.isoformat()[:-3] + 'Z'}
                for attribute in ["bypass_version_checks", "device_name", "device_type", "device_type_long",
                                  "device_version", "easyucs_version", "is_hidden", "is_system", "key_id",
                                  "system_usage", "target", "username"]:
                    if getattr(device_metadata_list[0], attribute) not in [None, ""]:
                        device_metadata[attribute] = getattr(device_metadata_list[0], attribute)
                device_dict = {"device": device_metadata}
                response = response_handle(device_dict, 200)
                return response
            else:
                response = response_handle(response="Device with UUID " + device_uuid + " not found", code=404)
        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/actions", methods=['GET'])
# @cross_origin()
def device_uuid_actions(device_uuid):
    if request.method == 'GET':
        try:
            device_metadata_list = easyucs.repository_manager.get_metadata(object_type="device", uuid=device_uuid)
            if len(device_metadata_list) == 1:
                device_type = device_metadata_list[0].device_type

                actions = None
                if device_type in ["cimc"]:
                    actions = ["claim_to_intersight", "clear_intersight_claim_status", "clear_sel_logs",
                               "clear_user_sessions", "erase_all_virtual_drives", "erase_all_flexflash", "reset",
                               "set_all_drives_status", "test_connection"]
                elif device_type in ["intersight"]:
                    actions = ["test_connection"]
                elif device_type in ["ucsc"]:
                    actions = ["clear_user_sessions", "test_connection"]
                elif device_type in ["ucsm"]:
                    actions = ["claim_to_intersight", "clear_intersight_claim_status", "clear_sel_logs",
                               "clear_user_sessions", "decommission_all_rack_servers", "erase_all_virtual_drives",
                               "erase_all_flexflash", "regenerate_certificate", "reset", "test_connection"]

                actions_dict = {"actions": actions}

                response = response_handle(actions_dict, 200)
            else:
                response = response_handle(response="Device with UUID " + device_uuid + " not found", code=404)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/actions/claim_to_intersight", methods=['POST'])
# @cross_origin()
def device_uuid_actions_claim_to_intersight(device_uuid):
    if request.method == 'POST':
        try:
            payload = request.json

            # Check if payload valid
            if payload:
                if not validate_json(json_data=payload, schema_path="api/specs/device_claim_to_intersight_post.json",
                                     logger=easyucs):
                    response = response_handle(code=400, response="Invalid payload")
                    return response

            intersight_device_uuid = payload.get("intersight_device_uuid", None)

            device = load_object(object_type="device", object_uuid=device_uuid)
            if device:
                # We need to find & potentially load the intersight device
                intersight_device = load_object(object_type="device", object_uuid=intersight_device_uuid)

                if not intersight_device:
                    response = response_handle(response="Could not find Intersight device with UUID: " +
                                                        str(intersight_device_uuid), code=404)
                    return response

                action_kwargs = {
                    "intersight_device": intersight_device
                }

                try:
                    # We create a new task
                    if device.metadata.device_type in ["cimc"]:
                        task_uuid = easyucs.task_manager.add_task(name="ClaimToIntersightUcsImc",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid),
                                                                  target_device_uuid=str(intersight_device.uuid))
                    elif device.metadata.device_type in ["ucsm"]:
                        task_uuid = easyucs.task_manager.add_task(name="ClaimToIntersightUcsSystem",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid),
                                                                  target_device_uuid=str(intersight_device.uuid))
                    else:
                        response = response_handle(response="Unsupported device type", code=500)
                        return response

                    # We schedule the claim to Intersight action through the scheduler
                    pending_task = {
                        "task_uuid": task_uuid,
                        "action_type": "claim_to_intersight",
                        "object_type": "device",
                        "timeout": timeout_values["claim_to_intersight"],
                        "action_kwargs": action_kwargs
                    }
                    if not easyucs.task_manager.add_to_pending_tasks(pending_task):
                        response = response_handle(response="Error while scheduling the task. Task Queue might be Full."
                                                            " Try again after some time.", code=400)
                        return response

                except Exception as err:
                    response = response_handle(response="Error while claiming devices to Intersight: " + str(err),
                                               code=500)
                    return response

                response = response_handle(response={"task": str(task_uuid)}, code=200)
            else:
                response = response_handle(response="Device not found with UUID: " + device_uuid, code=404)
        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/actions/clear_intersight_claim_status", methods=['POST'])
# @cross_origin()
def device_uuid_actions_clear_intersight_claim_status(device_uuid):
    if request.method == 'POST':
        try:
            device = load_object(object_type="device", object_uuid=device_uuid)
            if device:
                try:
                    # We create a new task
                    if device.metadata.device_type in ["cimc"]:
                        task_uuid = easyucs.task_manager.add_task(name="ClearIntersightClaimStatusUcsImc",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid))
                    elif device.metadata.device_type in ["ucsm"]:
                        task_uuid = easyucs.task_manager.add_task(name="ClearIntersightClaimStatusUcsSystem",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid))
                    else:
                        response = response_handle(response="Unsupported device type", code=500)
                        return response
                    # We schedule the clear Intersight claim status action through the scheduler
                    pending_task = {
                        "task_uuid": task_uuid,
                        "action_type": "clear_intersight_claim_status",
                        "object_type": "device",
                        "timeout": timeout_values["clear_intersight_claim_status"]
                    }
                    if not easyucs.task_manager.add_to_pending_tasks(pending_task):
                        response = response_handle(response="Error while scheduling the task. Task Queue might be Full."
                                                            " Try again after some time.", code=400)
                        return response
                except Exception as err:
                    response = response_handle(response="Error while performing clear Intersight Claim Status: " +
                                                        str(err), code=500)
                    return response
                response = response_handle(response={"task": str(task_uuid)}, code=200)

            else:
                response = response_handle(response="Device not found with UUID: " + device_uuid, code=404)

        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(response="Error while performing clear Intersight Claim Status: " + str(err),
                                       code=500)
        return response


@app.route("/devices/<device_uuid>/actions/clear_sel_logs", methods=['POST'])
# @cross_origin()
def device_uuid_actions_clear_sel_logs(device_uuid):
    if request.method == 'POST':
        try:
            device = load_object(object_type="device", object_uuid=device_uuid)
            if device:
                try:
                    # We create a new task
                    if device.metadata.device_type in ["cimc"]:
                        task_uuid = easyucs.task_manager.add_task(name="ClearSelLogsUcsImc",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid))
                    elif device.metadata.device_type in ["ucsm"]:
                        task_uuid = easyucs.task_manager.add_task(name="ClearSelLogsUcsSystem",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid))
                    else:
                        response = response_handle(response="Unsupported device type", code=500)
                        return response
                    # We schedule the clear SEL logs action through the scheduler
                    pending_task = {
                        "task_uuid": task_uuid,
                        "action_type": "clear_sel_logs",
                        "object_type": "device",
                        "timeout": timeout_values["clear_sel_logs"]
                    }
                    if not easyucs.task_manager.add_to_pending_tasks(pending_task):
                        response = response_handle(response="Error while scheduling the task. Task Queue might be Full."
                                                            " Try again after some time.", code=400)
                        return response
                except Exception as err:
                    response = response_handle(response="Error while performing clear SEL logs: " + str(err),
                                               code=500)
                    return response
                response = response_handle(response={"task": str(task_uuid)}, code=200)

            else:
                response = response_handle(response="Device not found with UUID: " + device_uuid, code=404)

        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(response="Error while performing clear SEL logs: " + str(err), code=500)
        return response


@app.route("/devices/<device_uuid>/actions/fetch_config_and_inventory", methods=['POST'])
# @cross_origin()
def device_uuid_actions_fetch_config_and_inventory(device_uuid):
    if request.method == 'POST':
        try:
            payload = request.json

            # Check if payload valid
            if payload:
                if not validate_json(json_data=payload, schema_path="api/specs/fetch.json",
                                     logger=easyucs):
                    response = response_handle(code=400, response="Invalid payload")
                    return response

            force = payload.get("force", False)

            device = load_object(object_type="device", object_uuid=device_uuid)
            if device:
                try:
                    # We create a new task
                    if device.metadata.device_type in ["ucsm"]:
                        task_uuid = easyucs.task_manager.add_task(name="FetchConfigInventoryUcsSystem",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid))
                    elif device.metadata.device_type in ["ucsc"]:
                        task_uuid = easyucs.task_manager.add_task(name="FetchConfigInventoryUcsCentral",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid))
                    else:
                        response = response_handle(response="Unsupported device type", code=500)
                        return response

                    # We schedule the fetch config & inventory action through the scheduler
                    pending_task = {
                        "task_uuid": task_uuid,
                        "action_type": "fetch",
                        "object_type": "config_inventory",
                        "timeout": timeout_values["fetch_config_inventory"],
                        "action_kwargs": {"force": force}
                    }
                    if not easyucs.task_manager.add_to_pending_tasks(pending_task):
                        response = response_handle(response="Error while scheduling the task. Task Queue might be Full."
                                                            " Try again after some time.", code=400)
                        return response

                except Exception as err:
                    response = response_handle(response="Error while fetching config and inventory: " + str(err),
                                               code=500)
                    return response

                response = response_handle(response={"task": str(task_uuid)}, code=200)
            else:
                response = response_handle(response="Device not found with UUID: " + device_uuid, code=404)
        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/actions/send_feedback", methods=['POST'])
# @cross_origin()
def device_uuid_actions_send_feedback(device_uuid):
    if request.method == 'POST':
        try:
            payload = request.json

            # Check if payload valid
            if payload:
                if not validate_json(json_data=payload, schema_path="api/specs/device_send_feedback_post.json",
                                     logger=easyucs):
                    response = response_handle(code=400, response="Invalid payload")
                    return response
            else:
                response = response_handle(code=400, response="Missing payload")
                return response

            device = load_object(object_type="device", object_uuid=device_uuid)
            if device:
                if device.metadata.device_type != "intersight":
                    response = response_handle(response="Device type is not Intersight", code=500)
                    return response

                if not device.send_feedback(
                        feedback_type=payload["feedback_type"], comment=payload["comment"],
                        follow_up=payload.get("follow_up", False), evaluation=payload.get("evaluation", "Excellent"),
                        alternative_follow_up_emails=payload.get("alternative_follow_up_emails", [])):
                    response = response_handle(response="Failed to send the feedback", code=500)
                    return response
                response = response_handle(code=200)
            else:
                response = response_handle(response="Device not found with UUID: " + device_uuid, code=404)
        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/actions/test_connection", methods=['POST'])
# @cross_origin()
def device_uuid_actions_test_connection(device_uuid):
    if request.method == 'POST':
        try:
            device = load_object(object_type="device", object_uuid=device_uuid)
            if device:
                if device.task is not None:
                    response = response_handle(response="Device already has a task running: " + str(device.task.uuid),
                                               code=500)
                    return response
                try:
                    # We create a new task
                    if device.metadata.device_type in ["cimc"]:
                        task_uuid = easyucs.task_manager.add_task(name="TestConnectionUcsImc",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid))
                    elif device.metadata.device_type in ["intersight"]:
                        task_uuid = easyucs.task_manager.add_task(name="TestConnectionIntersight",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid))
                    elif device.metadata.device_type in ["ucsc"]:
                        task_uuid = easyucs.task_manager.add_task(name="TestConnectionUcsCentral",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid))
                    elif device.metadata.device_type in ["ucsm"]:
                        task_uuid = easyucs.task_manager.add_task(name="TestConnectionUcsSystem",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid))
                    else:
                        response = response_handle(response="Unsupported device type", code=500)
                        return response

                    # We schedule the test connection action through the scheduler
                    pending_task = {
                        "task_uuid": task_uuid,
                        "action_type": "test_connection",
                        "object_type": "device",
                        "timeout": timeout_values["test_connection"]
                    }
                    if not easyucs.task_manager.add_to_pending_tasks(pending_task):
                        response = response_handle(response="Error while scheduling the task. Task Queue might be Full."
                                                            " Try again after some time.", code=400)
                        return response

                except Exception as err:
                    response = response_handle(response="Error testing the device connection: " + str(err),
                                               code=500)
                    return response

                response = response_handle(response={"task": str(task_uuid)}, code=200)
            else:
                response = response_handle(response="Device not found with UUID: " + device_uuid, code=404)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/backups", methods=['GET'])
# @cross_origin()
def device_uuid_backups(device_uuid):
    if request.method == 'GET':
        try:
            filter_attribute = request.args.get("filter_attribute", None)
            filter_type = request.args.get("filter_type", None)
            filter_value = request.args.get("filter_value", None)

            if filter_attribute is None or filter_type is None or filter_value is None:
                filter = None
            else:
                filter = [filter_attribute, filter_type, filter_value]

            order_by_attribute = request.args.get("order_by_attribute", None)
            order_by_direction = request.args.get("order_by_direction", None)

            if order_by_attribute is None or order_by_direction is None:
                order_by = None
            else:
                order_by = (order_by_attribute, order_by_direction)

            page_size = request.args.get("page_size", None)
            page_number = int(request.args.get("page_number", 0))

            backup_dict = get_object_from_db(object_type="backup", device_uuid=device_uuid, filter=filter,
                                             page_size=page_size, page_number=page_number, order_by=order_by)

            if backup_dict:
                response = response_handle(backup_dict, 200)
            else:
                response = response_handle(code=500,
                                           response="Unexpected error while fetching data from DB. Please check logs.")

        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/backups/actions", methods=['GET'])
# @cross_origin()
def device_uuid_backups_actions(device_uuid):
    if request.method == 'GET':
        try:
            device_metadata_list = easyucs.repository_manager.get_metadata(object_type="device", uuid=device_uuid)
            if len(device_metadata_list) == 1:
                device_type = device_metadata_list[0].device_type

                actions = None
                if device_type in ["cimc", "ucsc", "ucsm"]:
                    actions = ["fetch"]

                actions_dict = {"actions": actions}

                response = response_handle(actions_dict, 200)
            else:
                response = response_handle(response="Device with UUID " + device_uuid + " not found", code=404)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/backups/actions/delete", methods=['POST'])
# @cross_origin()
def device_uuid_backups_actions_delete(device_uuid):
    if request.method == 'POST':
        try:
            payload = request.json

            # Check if payload valid
            if not validate_json(json_data=payload, schema_path="api/specs/backup_delete.json", logger=easyucs):
                response = response_handle(code=400, response="Invalid payload")
                return response

            if "backup_uuids" in payload:
                backup_uuid_list = payload["backup_uuids"]

            backups_metadata_list = []

            for backup_uuid in backup_uuid_list:
                backup_metadata_list = easyucs.repository_manager.get_metadata(object_type="backup", uuid=backup_uuid)
                if len(backup_metadata_list) == 1:
                    backups_metadata_list.append(backup_metadata_list[0])
                else:
                    response = response_handle(response="Backup with UUID " + backup_uuid + " not found", code=404)
                    return response

            if easyucs.repository_manager.delete_bulk_from_repository(metadata_list=backups_metadata_list):
                response = response_handle(code=200)
            else:
                response = response_handle(code=500, response="Could not delete backups from repository")

        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/backups/actions/fetch", methods=['POST'])
# @cross_origin()
def device_uuid_backups_actions_fetch(device_uuid):
    if request.method == 'POST':
        try:
            action_kwargs = {
                "use_repository": True,
                "timeout": timeout_values["fetch_backup"] - 60
            }

            payload = request.json
            # Check if payload valid
            if payload:
                if not validate_json(json_data=payload, schema_path="api/specs/backup_fetch_post.json", logger=easyucs):
                    response = response_handle(code=400, response="Invalid payload")
                    return response
                if "backup_type" in payload:
                    action_kwargs["backup_type"] = payload["backup_type"]

            device = load_object(object_type="device", object_uuid=device_uuid)
            if device:
                try:
                    # We create a new task
                    if device.metadata.device_type in ["cimc"]:
                        task_uuid = easyucs.task_manager.add_task(name="FetchBackupUcsImc",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid))
                    elif device.metadata.device_type in ["ucsc"]:
                        task_uuid = easyucs.task_manager.add_task(name="FetchBackupUcsCentral",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid))
                    elif device.metadata.device_type in ["ucsm"]:
                        task_uuid = easyucs.task_manager.add_task(name="FetchBackupUcsSystem",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid))
                    else:
                        response = response_handle(response="Unsupported device type", code=500)
                        return response

                    # We schedule the fetch backup action through the scheduler
                    pending_task = {
                        "task_uuid": task_uuid,
                        "action_type": "fetch",
                        "object_type": "backup",
                        "timeout": timeout_values["fetch_backup"],
                        "action_kwargs": action_kwargs
                    }
                    if not easyucs.task_manager.add_to_pending_tasks(pending_task):
                        response = response_handle(response="Error while scheduling the task. Task Queue might be Full."
                                                            " Try again after some time.", code=400)
                        return response

                except Exception as err:
                    response = response_handle(response="Error while fetching backup: " + str(err), code=500)
                    return response

                response = response_handle(response={"task": str(task_uuid)}, code=200)
            else:
                response = response_handle(response="Device not found with UUID: " + device_uuid, code=404)
        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/backups/<backup_uuid>", methods=['GET', 'DELETE'])
# @cross_origin()
def device_uuid_backup_uuid(device_uuid, backup_uuid):
    if request.method == 'GET':
        try:
            filter_attribute = request.args.get("filter_attribute", None)
            filter_type = request.args.get("filter_type", None)
            filter_value = request.args.get("filter_value", None)

            if filter_attribute is None or filter_type is None or filter_value is None:
                filter = None
            else:
                filter = [filter_attribute, filter_type, filter_value]

            order_by_attribute = request.args.get("order_by_attribute", None)
            order_by_direction = request.args.get("order_by_direction", None)

            if order_by_attribute is None or order_by_direction is None:
                order_by = None
            else:
                order_by = (order_by_attribute, order_by_direction)

            page_size = request.args.get("page_size", None)
            page_number = int(request.args.get("page_number", 0))

            backup_dict = get_object_from_db(object_type="backup", uuid=backup_uuid, filter=filter, page_size=page_size,
                                             page_number=page_number, order_by=order_by)

            if backup_dict:
                response = response_handle(backup_dict, 200)
            else:
                response = response_handle(code=500, response=f"Could not find backup with UUID {backup_uuid}")

        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response

    if request.method == 'DELETE':
        try:
            backup_metadata_list = easyucs.repository_manager.get_metadata(object_type="backup", uuid=backup_uuid)
            if len(backup_metadata_list) == 1:
                if easyucs.repository_manager.delete_from_repository(metadata=backup_metadata_list[0]):
                    response = response_handle(code=200)
                else:
                    response = response_handle(code=500, response="Could not delete backup from repository")
            else:
                response = response_handle(response="Backup with UUID " + backup_uuid + " not found", code=404)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/backups/<backup_uuid>/actions", methods=['GET'])
# @cross_origin()
def device_uuid_backup_uuid_actions(device_uuid, backup_uuid):
    if request.method == 'GET':
        try:
            device_metadata_list = easyucs.repository_manager.get_metadata(object_type="device", uuid=device_uuid)
            if len(device_metadata_list) == 1:
                device_type = device_metadata_list[0].device_type

                backup_metadata_list = easyucs.repository_manager.get_metadata(object_type="backup", uuid=backup_uuid)
                if len(backup_metadata_list) == 1:

                    actions = None
                    if device_type in ["cimc", "ucsc", "ucsm"]:
                        actions = ["download"]

                    actions_dict = {"actions": actions}

                    response = response_handle(actions_dict, 200)

                else:
                    response = response_handle(response="Backup with UUID " + backup_uuid + " not found",
                                               code=404)
            else:
                response = response_handle(response="Device with UUID " + device_uuid + " not found", code=404)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/backups/<backup_uuid>/actions/download", methods=['GET'])
# @cross_origin()
def device_uuid_backup_uuid_actions_download(device_uuid, backup_uuid):
    if request.method == 'GET':
        try:
            # Trying to find device using its metadata (DB)
            easyucs.logger(level="debug", message="Trying to find device with UUID " + device_uuid + " from the DB")
            device_metadata_list = easyucs.repository_manager.get_metadata(object_type="device", uuid=device_uuid)
            if len(device_metadata_list) != 1:
                response = response_handle(response="Device not found with UUID: " + device_uuid, code=404)
                return response

            # Trying to find backup using its metadata (DB)
            easyucs.logger(level="debug",
                           message="Trying to find backup with UUID " + backup_uuid + " from the DB")
            backup_metadata_list = easyucs.repository_manager.get_metadata(object_type="backup", uuid=backup_uuid)
            if len(backup_metadata_list) == 1:
                file_path = os.path.abspath(os.path.join(EASYUCS_ROOT, backup_metadata_list[0].file_path))
                response = send_file(file_path, as_attachment=True,
                                     download_name="backup_" + backup_uuid +
                                                   backup_metadata_list[0].backup_file_extension)
            else:
                response = response_handle(response="Backup not found with UUID: " + backup_uuid, code=404)

        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/cache", methods=['GET'])
def device_uuid_cache(device_uuid):
    if request.method == 'GET':
        try:
            device = load_object(object_type="device", object_uuid=device_uuid)
            if device:
                operating_system = request.args.get("os", True, type=lambda v: v.lower() == 'true')
                firmware = request.args.get("firmware", True, type=lambda v: v.lower() == 'true')

                if device.metadata.device_type not in ["intersight"]:
                    response = response_handle(response=f"OS and Firmware cached data not supported for "
                                                        f"{device.metadata.device_type_long} device",
                                               code=404)
                    return response

                if not device.metadata.cache_path:
                    response = response_handle(response="OS and Firmware cached data not found", code=404)
                    return response

                os_firmware_data = device.get_os_firmware_data()

                if not os_firmware_data:
                    response = response_handle(response="Failed to read OS and Firmware cached data from device",
                                               code=500)
                    return response

                if not operating_system:
                    del os_firmware_data["os"]
                if not firmware:
                    del os_firmware_data["firmware"]

                response = response_handle(response=os_firmware_data, code=200)
            else:
                response = response_handle(response="Device not found with UUID: " + device_uuid, code=404)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/configs", methods=['GET', 'POST'])
# @cross_origin()
def device_uuid_configs(device_uuid):
    if request.method == 'GET':
        try:
            filter_attribute = request.args.get("filter_attribute", None)
            filter_type = request.args.get("filter_type", None)
            filter_value = request.args.get("filter_value", None)

            if filter_attribute is None or filter_type is None or filter_value is None:
                filter = None
            else:
                filter = [filter_attribute, filter_type, filter_value]

            order_by_attribute = request.args.get("order_by_attribute", None)
            order_by_direction = request.args.get("order_by_direction", None)

            if order_by_attribute is None or order_by_direction is None:
                order_by = None
            else:
                order_by = (order_by_attribute, order_by_direction)

            page_size = request.args.get("page_size", None)
            page_number = int(request.args.get("page_number", 0))

            config_dict = get_object_from_db(object_type="config", device_uuid=device_uuid, filter=filter,
                                             page_size=page_size, page_number=page_number, order_by=order_by)

            if config_dict:
                response = response_handle(config_dict, 200)
            else:
                response = response_handle(code=500,
                                           response="Unexpected error while fetching data from DB. Please check logs.")
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response

    if request.method == 'POST':
        try:
            device = load_object(object_type="device", object_uuid=device_uuid)
            if device:
                try:
                    file = request.files['config_file']
                    json_file = file.read().decode("utf-8")
                except Exception as err:  # FIXME: Handle various exception cases - should only support JSON files
                    response = response_handle(response="Error while reading config file: " + str(err), code=500)
                    return response

                config = device.config_manager.import_config(import_format="json", config=json_file)
                if config:
                    # Save the imported config
                    easyucs.repository_manager.save_to_repository(object=config)

                    config_metadata = {"device_uuid": str(config.metadata.device_uuid),
                                       "timestamp": config.metadata.timestamp.isoformat()[:-3] + 'Z',
                                       "uuid": str(config.metadata.uuid)}
                    for attribute in ["category", "device_version", "easyucs_version", "is_hidden", "is_system", "name",
                                      "origin", "revision", "subcategory", "system_usage", "url"]:
                        if getattr(config.metadata, attribute) not in [None, ""]:
                            config_metadata[attribute] = getattr(config.metadata, attribute)

                    for attribute in ["source_config_uuid", "source_device_uuid", "source_inventory_uuid"]:
                        if getattr(config.metadata, attribute) not in [None, ""]:
                            config_metadata[attribute] = str(getattr(config.metadata, attribute))

                    config_dict = {"config": config_metadata}
                    response = response_handle(config_dict, 200)
                else:
                    response_message = easyucs.api_error_message
                    if not response_message:
                        response_message = "Impossible to import the file as a config"
                    response = response_handle(response=response_message, code=500)
            else:
                response = response_handle(response="Device not found with UUID: " + device_uuid, code=404)
        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/configs/actions", methods=['GET'])
# @cross_origin()
def device_uuid_configs_actions(device_uuid):
    if request.method == 'GET':
        try:
            device_metadata_list = easyucs.repository_manager.get_metadata(object_type="device", uuid=device_uuid)
            if len(device_metadata_list) == 1:
                device_type = device_metadata_list[0].device_type

                actions = None
                if device_type in ["cimc", "intersight", "ucsc", "ucsm"]:
                    actions = ["fetch"]

                actions_dict = {"actions": actions}

                response = response_handle(actions_dict, 200)
            else:
                response = response_handle(response="Device with UUID " + device_uuid + " not found", code=404)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/configs/actions/delete", methods=['POST'])
# @cross_origin()
def device_uuid_configs_actions_delete(device_uuid):
    if request.method == 'POST':
        try:
            payload = request.json

            # Check if payload valid
            if not validate_json(json_data=payload, schema_path="api/specs/config_delete.json", logger=easyucs):
                response = response_handle(code=400, response="Invalid payload")
                return response

            if "config_uuids" in payload:
                config_uuid_list = payload["config_uuids"]

            configs_uuid_list = []

            for config_uuid in config_uuid_list:
                config_metadata_list = easyucs.repository_manager.get_metadata(object_type="config", uuid=config_uuid)
                if len(config_metadata_list) == 1:
                    configs_uuid_list.append(config_metadata_list[0])
                else:
                    response = response_handle(response="Config with UUID " + config_uuid + " not found", code=404)
                    return response

            if easyucs.repository_manager.delete_bulk_from_repository(metadata_list=configs_uuid_list):
                response = response_handle(code=200)
            else:
                response = response_handle(code=500, response="Could not delete configs from repository")

        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/configs/actions/fetch", methods=['POST'])
# @cross_origin()
def device_uuid_configs_actions_fetch(device_uuid):
    if request.method == 'POST':
        try:
            payload = request.json

            # Check if payload valid
            if payload:
                if not validate_json(json_data=payload, schema_path="api/specs/fetch.json",
                                     logger=easyucs):
                    response = response_handle(code=400, response="Invalid payload")
                    return response

            force = payload.get("force", False)

            device = load_object(object_type="device", object_uuid=device_uuid)
            if device:
                try:
                    # We create a new task
                    if device.metadata.device_type in ["cimc"]:
                        task_uuid = easyucs.task_manager.add_task(name="FetchConfigUcsImc",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid))
                    elif device.metadata.device_type in ["intersight"]:
                        task_uuid = easyucs.task_manager.add_task(name="FetchConfigIntersight",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid))
                    elif device.metadata.device_type in ["ucsc"]:
                        task_uuid = easyucs.task_manager.add_task(name="FetchConfigUcsCentral",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid))
                    elif device.metadata.device_type in ["ucsm"]:
                        task_uuid = easyucs.task_manager.add_task(name="FetchConfigUcsSystem",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid))
                    else:
                        response = response_handle(response="Unsupported device type", code=500)
                        return response

                    # We schedule the fetch config action through the scheduler
                    pending_task = {
                        "task_uuid": task_uuid,
                        "action_type": "fetch",
                        "object_type": "config",
                        "timeout": timeout_values["fetch_config"],
                        "action_kwargs": {"force": force}
                    }
                    if not easyucs.task_manager.add_to_pending_tasks(pending_task):
                        response = response_handle(response="Error while scheduling the task. Task Queue might be Full."
                                                            " Try again after some time.", code=400)
                        return response

                except Exception as err:
                    response = response_handle(response="Error while fetching config: " + str(err), code=500)
                    return response

                response = response_handle(response={"task": str(task_uuid)}, code=200)
            else:
                response = response_handle(response="Device not found with UUID: " + device_uuid, code=404)
        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/configs/<config_uuid>", methods=['GET', 'DELETE'])
# @cross_origin()
def device_uuid_config_uuid(device_uuid, config_uuid):
    if request.method == 'GET':
        try:
            filter_attribute = request.args.get("filter_attribute", None)
            filter_type = request.args.get("filter_type", None)
            filter_value = request.args.get("filter_value", None)

            if filter_attribute is None or filter_type is None or filter_value is None:
                filter = None
            else:
                filter = [filter_attribute, filter_type, filter_value]

            order_by_attribute = request.args.get("order_by_attribute", None)
            order_by_direction = request.args.get("order_by_direction", None)

            if order_by_attribute is None or order_by_direction is None:
                order_by = None
            else:
                order_by = (order_by_attribute, order_by_direction)

            page_size = request.args.get("page_size", None)
            page_number = int(request.args.get("page_number", 0))

            config_dict = get_object_from_db(object_type="config", uuid=config_uuid, filter=filter,
                                             page_size=page_size, page_number=page_number, order_by=order_by)

            if config_dict:
                response = response_handle(config_dict, 200)
            else:
                response = response_handle(code=500, response=f"Could not find config with UUID {config_uuid}")

        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response

    if request.method == 'DELETE':
        try:
            config_metadata_list = easyucs.repository_manager.get_metadata(object_type="config", uuid=config_uuid)
            if len(config_metadata_list) == 1:
                if easyucs.repository_manager.delete_from_repository(metadata=config_metadata_list[0]):
                    response = response_handle(code=200)
                else:
                    response = response_handle(code=500, response="Could not delete config from repository")
            else:
                response = response_handle(response="Config with UUID " + config_uuid + " not found", code=404)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/configs/<config_uuid>/actions", methods=['GET'])
# @cross_origin()
def device_uuid_config_uuid_actions(device_uuid, config_uuid):
    if request.method == 'GET':
        try:
            device_metadata_list = easyucs.repository_manager.get_metadata(object_type="device", uuid=device_uuid)
            if len(device_metadata_list) == 1:
                device_type = device_metadata_list[0].device_type

                config_metadata_list = easyucs.repository_manager.get_metadata(object_type="config", uuid=config_uuid)
                if len(config_metadata_list) == 1:

                    actions = None
                    if device_type in ["cimc", "intersight", "ucsc", "ucsm"]:
                        actions = ["download", "push"]

                    actions_dict = {"actions": actions}

                    response = response_handle(actions_dict, 200)

                else:
                    response = response_handle(response="Config with UUID " + config_uuid + " not found", code=404)
            else:
                response = response_handle(response="Device with UUID " + device_uuid + " not found", code=404)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/configs/<config_uuid>/actions/download", methods=['GET'])
# @cross_origin()
def device_uuid_config_uuid_actions_download(device_uuid, config_uuid):
    if request.method == 'GET':
        try:
            # Trying to find device using its metadata (DB)
            easyucs.logger(level="debug", message="Trying to find device with UUID " + device_uuid + " from the DB")
            device_metadata_list = easyucs.repository_manager.get_metadata(object_type="device", uuid=device_uuid)
            if len(device_metadata_list) != 1:
                response = response_handle(response="Device not found with UUID: " + device_uuid, code=404)
                return response

            # Trying to find config using its metadata (DB)
            easyucs.logger(level="debug", message="Trying to find config with UUID " + config_uuid + " from the DB")
            config_metadata_list = easyucs.repository_manager.get_metadata(object_type="config", uuid=config_uuid)
            if len(config_metadata_list) == 1:
                file_path = os.path.abspath(os.path.join(EASYUCS_ROOT, config_metadata_list[0].file_path))
                response = send_file(file_path, as_attachment=True, download_name="config_" + config_uuid + ".json")
            else:
                response = response_handle(response="Config not found with UUID: " + config_uuid, code=404)

        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/configs/<config_uuid>/actions/push", methods=['POST'])
# @cross_origin()
def device_uuid_config_uuid_actions_push(device_uuid, config_uuid):
    if request.method == 'POST':
        try:
            action_kwargs = {
                "uuid": config_uuid,
                "bypass_version_checks": True
            }

            payload = request.json
            # Check if payload valid
            if payload:
                if not validate_json(json_data=payload, schema_path="api/specs/config_push_post.json", logger=easyucs):
                    response = response_handle(code=400, response="Invalid payload")
                    return response
                if "fi_ip_list" in payload:
                    action_kwargs["fi_ip_list"] = payload["fi_ip_list"]
                if "reset" in payload:
                    action_kwargs["reset"] = payload["reset"]

            device = load_object(object_type="device", object_uuid=device_uuid)
            if device:
                # We first need to find & potentially load the config
                if config_uuid:
                    config = load_object(object_type="config", object_uuid=config_uuid, device=device)
                    if not config:
                        response_message = easyucs.api_error_message
                        if not response_message:
                            response_message = "Failed to load config with UUID " + str(config_uuid)
                        response = response_handle(response=response_message, code=400)
                        return response

                try:
                    # We create a new task
                    if device.metadata.device_type in ["cimc"]:
                        task_uuid = easyucs.task_manager.add_task(
                            name="PushConfigUcsImc", device_name=str(device.name), device_uuid=str(device.uuid),
                            config_uuid=str(config.uuid)
                        )
                    elif device.metadata.device_type in ["intersight"]:
                        task_uuid = easyucs.task_manager.add_task(
                            name="PushConfigIntersight", device_name=str(device.name), device_uuid=str(device.uuid),
                            config_uuid=str(config.uuid)
                        )
                    elif device.metadata.device_type in ["ucsc"]:
                        task_uuid = easyucs.task_manager.add_task(
                            name="PushConfigUcsCentral", device_name=str(device.name), device_uuid=str(device.uuid),
                            config_uuid=str(config.uuid)
                        )
                    elif device.metadata.device_type in ["ucsm"]:
                        task_uuid = easyucs.task_manager.add_task(
                            name="PushConfigUcsSystem", device_name=str(device.name), device_uuid=str(device.uuid),
                            config_uuid=str(config.uuid)
                        )
                    else:
                        response = response_handle(response="Unsupported device type", code=500)
                        return response

                    # We schedule the push config action through the scheduler
                    pending_task = {
                        "task_uuid": task_uuid,
                        "action_type": "push",
                        "object_type": "config",
                        "timeout": timeout_values["push_config"],
                        "action_kwargs": action_kwargs
                    }
                    if not easyucs.task_manager.add_to_pending_tasks(pending_task):
                        response = response_handle(response="Error while scheduling the task. Task Queue might be Full."
                                                            " Try again after some time.", code=400)
                        return response

                except Exception as err:
                    response = response_handle(response="Error while pushing config: " + str(err), code=500)
                    return response

                response = response_handle(response={"task": str(task_uuid)}, code=200)
            else:
                response = response_handle(response="Device not found with UUID: " + device_uuid, code=404)
        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/configs/<config_uuid>/profiles", methods=['GET'])
def device_uuid_config_uuid_profiles(device_uuid, config_uuid):
    if request.method == "GET":
        try:
            device = load_object(object_type="device", object_uuid=device_uuid)
            if device:
                config = load_object(object_type="config", object_uuid=config_uuid, device=device)
                if not config:
                    response_message = easyucs.api_error_message
                    if not response_message:
                        response_message = "Failed to load config with UUID " + str(config_uuid)
                    response = response_handle(response=response_message, code=400)
                    return response

                service_profiles = device.config_manager.get_profiles(config=config)
                if service_profiles:
                    response = response_handle(response=service_profiles, code=200)
                else:
                    response = response_handle(response=[], code=200)
            else:
                response = response_handle(response="Device not found with UUID: " + device_uuid, code=404)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/inventories", methods=['GET', 'POST'])
# @cross_origin()
def device_uuid_inventories(device_uuid):
    if request.method == 'GET':
        try:
            filter_attribute = request.args.get("filter_attribute", None)
            filter_type = request.args.get("filter_type", None)
            filter_value = request.args.get("filter_value", None)

            if filter_attribute is None or filter_type is None or filter_value is None:
                filter = None
            else:
                filter = [filter_attribute, filter_type, filter_value]

            order_by_attribute = request.args.get("order_by_attribute", None)
            order_by_direction = request.args.get("order_by_direction", None)

            if order_by_attribute is None or order_by_direction is None:
                order_by = None
            else:
                order_by = (order_by_attribute, order_by_direction)

            page_size = request.args.get("page_size", None)
            page_number = int(request.args.get("page_number", 0))

            inventory_dict = get_object_from_db(object_type="inventory", device_uuid=device_uuid, filter=filter,
                                                page_size=page_size, page_number=page_number, order_by=order_by)

            if inventory_dict:
                response = response_handle(inventory_dict, 200)
            else:
                response = response_handle(code=500,
                                           response="Unexpected error while fetching data from DB. Please check logs.")

        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response

    if request.method == 'POST':
        try:
            device = load_object(object_type="device", object_uuid=device_uuid)
            if device:
                try:
                    file = request.files['inventory_file']
                    json_file = file.read().decode("utf-8")
                except Exception as err:  # FIXME: Handle various exception cases - should only support JSON files
                    response = response_handle(response="Error while reading inventory file: " + str(err), code=500)
                    return response

                inventory = device.inventory_manager.import_inventory(import_format="json", inventory=json_file)
                if inventory:
                    # Save the imported inventory
                    easyucs.repository_manager.save_to_repository(object=inventory)

                    inventory_metadata = {"device_uuid": str(inventory.metadata.device_uuid),
                                          "timestamp": inventory.metadata.timestamp.isoformat()[:-3] + 'Z',
                                          "uuid": str(inventory.metadata.uuid)}
                    for attribute in ["device_version", "easyucs_version", "is_hidden", "is_system", "name", "origin",
                                      "system_usage"]:
                        if getattr(inventory.metadata, attribute) not in [None, ""]:
                            inventory_metadata[attribute] = getattr(inventory.metadata, attribute)

                    inventory_dict = {"inventory": inventory_metadata}
                    response = response_handle(inventory_dict, 200)
                else:
                    response = response_handle(response="Impossible to import the file as an inventory", code=500)
            else:
                response = response_handle(response="Device not found with UUID: " + device_uuid, code=404)
        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/inventories/actions", methods=['GET'])
# @cross_origin()
def device_uuid_inventories_actions(device_uuid):
    if request.method == 'GET':
        try:
            device_metadata_list = easyucs.repository_manager.get_metadata(object_type="device", uuid=device_uuid)
            if len(device_metadata_list) == 1:
                device_type = device_metadata_list[0].device_type

                actions = None
                if device_type in ["cimc", "intersight", "ucsc", "ucsm"]:
                    actions = ["fetch"]

                actions_dict = {"actions": actions}

                response = response_handle(actions_dict, 200)
            else:
                response = response_handle(response="Device with UUID " + device_uuid + " not found", code=404)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/inventories/actions/delete", methods=['POST'])
# @cross_origin()
def device_uuid_inventories_actions_delete(device_uuid):
    if request.method == 'POST':
        try:
            payload = request.json

            # Check if payload valid
            if not validate_json(json_data=payload, schema_path="api/specs/inventory_delete.json", logger=easyucs):
                response = response_handle(code=400, response="Invalid payload")
                return response

            if "inventory_uuids" in payload:
                inventory_uuid_list = payload["inventory_uuids"]

            inventories_uuid_list = []

            for inventory_uuid in inventory_uuid_list:
                inventory_metadata_list = easyucs.repository_manager.get_metadata(object_type="inventory",
                                                                                  uuid=inventory_uuid)
                if len(inventory_metadata_list) == 1:
                    inventories_uuid_list.append(inventory_metadata_list[0])
                else:
                    response = response_handle(response="Inventory with UUID " + inventory_uuid + " not found",
                                               code=404)
                    return response

            if easyucs.repository_manager.delete_bulk_from_repository(metadata_list=inventories_uuid_list):
                response = response_handle(code=200)
            else:
                response = response_handle(code=500, response="Could not delete inventories from repository")

        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/inventories/actions/fetch", methods=['POST'])
# @cross_origin()
def device_uuid_inventories_actions_fetch(device_uuid):
    if request.method == 'POST':
        try:
            payload = request.json

            # Check if payload valid
            if payload:
                if not validate_json(json_data=payload, schema_path="api/specs/fetch.json",
                                     logger=easyucs):
                    response = response_handle(code=400, response="Invalid payload")
                    return response

            force = payload.get("force", False)

            device = load_object(object_type="device", object_uuid=device_uuid)
            if device:
                try:
                    # We create a new task
                    if device.metadata.device_type in ["cimc"]:
                        task_uuid = easyucs.task_manager.add_task(name="FetchInventoryUcsImc",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid))
                    elif device.metadata.device_type in ["intersight"]:
                        task_uuid = easyucs.task_manager.add_task(name="FetchInventoryIntersight",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid))
                    elif device.metadata.device_type in ["ucsc"]:
                        task_uuid = easyucs.task_manager.add_task(name="FetchInventoryUcsCentral",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid))
                    elif device.metadata.device_type in ["ucsm"]:
                        task_uuid = easyucs.task_manager.add_task(name="FetchInventoryUcsSystem",
                                                                  device_name=str(device.name),
                                                                  device_uuid=str(device.uuid))
                    else:
                        response = response_handle(response="Unsupported device type", code=500)
                        return response

                    # We schedule the fetch inventory action through the scheduler
                    pending_task = {
                        "task_uuid": task_uuid,
                        "action_type": "fetch",
                        "object_type": "inventory",
                        "timeout": timeout_values["fetch_inventory"],
                        "action_kwargs": {"force": force}
                    }
                    if not easyucs.task_manager.add_to_pending_tasks(pending_task):
                        response = response_handle(response="Error while scheduling the task. Task Queue might be Full."
                                                            " Try again after some time.", code=400)
                        return response

                except Exception as err:
                    response = response_handle(response="Error while fetching inventory: " + str(err), code=500)
                    return response

                response = response_handle(response={"task": str(task_uuid)}, code=200)
            else:
                response = response_handle(response="Device not found with UUID: " + device_uuid, code=404)
        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/inventories/<inventory_uuid>", methods=['GET', 'DELETE'])
# @cross_origin()
def device_uuid_inventory_uuid(device_uuid, inventory_uuid):
    if request.method == 'GET':
        try:
            filter_attribute = request.args.get("filter_attribute", None)
            filter_type = request.args.get("filter_type", None)
            filter_value = request.args.get("filter_value", None)

            if filter_attribute is None or filter_type is None or filter_value is None:
                filter = None
            else:
                filter = [filter_attribute, filter_type, filter_value]

            page_size = request.args.get("page_size", None)
            page_number = int(request.args.get("page_number", 0))

            order_by_attribute = request.args.get("order_by_attribute", None)
            order_by_direction = request.args.get("order_by_direction", None)

            if order_by_attribute is None or order_by_direction is None:
                order_by = None
            else:
                order_by = (order_by_attribute, order_by_direction)

            inventory_dict = get_object_from_db(object_type="inventory", uuid=inventory_uuid, filter=filter,
                                                page_size=page_size, page_number=page_number, order_by=order_by)

            if inventory_dict:
                response = response_handle(inventory_dict, 200)
            else:
                response = response_handle(code=500, response=f"Could not find inventory with UUID {inventory_uuid}")

        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response

    if request.method == 'DELETE':
        try:
            inventory_metadata_list = easyucs.repository_manager.get_metadata(object_type="inventory",
                                                                              uuid=inventory_uuid)
            if len(inventory_metadata_list) == 1:
                if easyucs.repository_manager.delete_from_repository(metadata=inventory_metadata_list[0]):
                    response = response_handle(code=200)
                else:
                    response = response_handle(code=500, response="Could not delete inventory from repository")
            else:
                response = response_handle(response="Inventory with UUID " + inventory_uuid + " not found", code=404)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/inventories/<inventory_uuid>/actions", methods=['GET'])
# @cross_origin()
def device_uuid_inventory_uuid_actions(device_uuid, inventory_uuid):
    if request.method == 'GET':
        try:
            device_metadata_list = easyucs.repository_manager.get_metadata(object_type="device", uuid=device_uuid)
            if len(device_metadata_list) == 1:
                device_type = device_metadata_list[0].device_type

                inventory_metadata_list = easyucs.repository_manager.get_metadata(object_type="inventory",
                                                                                  uuid=inventory_uuid)
                if len(inventory_metadata_list) == 1:

                    actions = None
                    if device_type in ["cimc", "intersight", "ucsc", "ucsm"]:
                        actions = ["download"]

                    actions_dict = {"actions": actions}

                    response = response_handle(actions_dict, 200)

                else:
                    response = response_handle(response="Inventory with UUID " + inventory_uuid + " not found",
                                               code=404)
            else:
                response = response_handle(response="Device with UUID " + device_uuid + " not found", code=404)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/inventories/<inventory_uuid>/actions/download", methods=['GET'])
# @cross_origin()
def device_uuid_inventory_uuid_actions_download(device_uuid, inventory_uuid):
    if request.method == 'GET':
        try:
            # Trying to find device using its metadata (DB)
            easyucs.logger(level="debug", message="Trying to find device with UUID " + device_uuid + " from the DB")
            device_metadata_list = easyucs.repository_manager.get_metadata(object_type="device", uuid=device_uuid)
            if len(device_metadata_list) != 1:
                response = response_handle(response="Device not found with UUID: " + device_uuid, code=404)
                return response

            # Trying to find inventory using its metadata (DB)
            easyucs.logger(level="debug",
                           message="Trying to find inventory with UUID " + inventory_uuid + " from the DB")
            inventory_metadata_list = easyucs.repository_manager.get_metadata(object_type="inventory",
                                                                              uuid=inventory_uuid)
            if len(inventory_metadata_list) == 1:
                file_path = os.path.abspath(os.path.join(EASYUCS_ROOT, inventory_metadata_list[0].file_path))
                response = send_file(file_path, as_attachment=True,
                                     download_name="inventory_" + inventory_uuid + ".json")
            else:
                response = response_handle(response="Inventory not found with UUID: " + inventory_uuid, code=404)

        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/logs", methods=['GET'])
def device_uuid_logs(device_uuid):
    if request.method == 'GET':
        try:
            device = load_object(object_type="device", object_uuid=device_uuid)
            if device:
                try:
                    device_logs = device.get_logs()
                    if not device_logs:
                        logs_dict = {"logs": None}
                    else:
                        logs_dict = {"logs": device_logs}
                    response = response_handle(logs_dict, 200)

                except Exception as err:
                    response = response_handle(response="Error while getting device logs: " + str(err), code=500)
                    return response
            else:
                response = response_handle(response="Device with UUID " + device_uuid + " not found", code=404)
                return response

        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/orgs", methods=['GET'])
def device_uuid_orgs(device_uuid):
    if request.method == "GET":
        try:
            device = load_object(object_type="device", object_uuid=device_uuid)
            if device:
                if device.metadata.device_type not in ["intersight"]:
                    response = response_handle(response="Invalid device type", code=400)
                    return response

                cached_orgs = device.metadata.cached_orgs
                if cached_orgs:
                    response = response_handle(response={"orgs": cached_orgs}, code=200)
                else:
                    response = response_handle(response="No cached orgs found. Please check device connection.",
                                               code=500)
            else:
                response = response_handle(response="Device not found with UUID: " + device_uuid, code=404)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/reports", methods=['GET'])
# @cross_origin()
def device_uuid_reports(device_uuid):
    if request.method == 'GET':
        try:
            filter_attribute = request.args.get("filter_attribute", None)
            filter_type = request.args.get("filter_type", None)
            filter_value = request.args.get("filter_value", None)

            if filter_attribute is None or filter_type is None or filter_value is None:
                filter = None
            else:
                filter = [filter_attribute, filter_type, filter_value]

            order_by_attribute = request.args.get("order_by_attribute", None)
            order_by_direction = request.args.get("order_by_direction", None)

            if order_by_attribute is None or order_by_direction is None:
                order_by = None
            else:
                order_by = (order_by_attribute, order_by_direction)

            page_size = request.args.get("page_size", None)
            page_number = int(request.args.get("page_number", 0))

            report_dict = get_object_from_db(object_type="report", device_uuid=device_uuid, filter=filter,
                                             page_size=page_size, page_number=page_number, order_by=order_by)

            if report_dict:
                response = response_handle(report_dict, 200)
            else:
                response = response_handle(code=500,
                                           response="Unexpected error while fetching data from DB. Please check logs.")

        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/reports/actions", methods=['GET'])
# @cross_origin()
def device_uuid_reports_actions(device_uuid):
    if request.method == 'GET':
        try:
            device_metadata_list = easyucs.repository_manager.get_metadata(object_type="device", uuid=device_uuid)
            if len(device_metadata_list) == 1:
                device_type = device_metadata_list[0].device_type

                actions = None
                if device_type in ["cimc", "ucsm"]:
                    actions = ["generate"]

                actions_dict = {"actions": actions}

                response = response_handle(actions_dict, 200)
            else:
                response = response_handle(response="Device with UUID " + device_uuid + " not found", code=404)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/reports/actions/delete", methods=['POST'])
# @cross_origin()
def device_uuid_reports_actions_delete(device_uuid):
    if request.method == 'POST':
        try:
            payload = request.json

            # Check if payload valid
            if not validate_json(json_data=payload, schema_path="api/specs/report_delete.json", logger=easyucs):
                response = response_handle(code=400, response="Invalid payload")
                return response

            if "report_uuids" in payload:
                report_uuid_list = payload["report_uuids"]

            reports_uuid_list = []

            for report_uuid in report_uuid_list:
                report_metadata_list = easyucs.repository_manager.get_metadata(object_type="report", uuid=report_uuid)
                if len(report_metadata_list) == 1:
                    reports_uuid_list.append(report_metadata_list[0])
                else:
                    response = response_handle(response="Report with UUID " + report_uuid + " not found", code=404)
                    return response

            if easyucs.repository_manager.delete_bulk_from_repository(metadata_list=reports_uuid_list):
                response = response_handle(code=200)
            else:
                response = response_handle(code=500, response="Could not delete reports from repository")

        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/reports/actions/generate", methods=['POST'])
# @cross_origin()
def device_uuid_reports_actions_generate(device_uuid):
    if request.method == 'POST':
        try:
            payload = request.json

            # Check if payload valid
            if not validate_json(json_data=payload, schema_path="api/specs/report_generate_post.json", logger=easyucs):
                response = response_handle(code=400, response="Invalid payload")
                return response

            config_uuid = payload.get("config_uuid", None)
            inventory_uuid = payload.get("inventory_uuid", None)

            action_kwargs = {}
            if "language" in payload:
                action_kwargs["language"] = payload["language"]
            if "output_formats" in payload:
                action_kwargs["output_formats"] = payload["output_formats"]
            if "page_layout" in payload:
                action_kwargs["page_layout"] = payload["page_layout"]
            if "size" in payload:
                action_kwargs["size"] = payload["size"]

            device = load_object(object_type="device", object_uuid=device_uuid)
            if device:
                # We first need to find & potentially load the config & inventory
                config = load_object(object_type="config", object_uuid=config_uuid, device=device)
                if config:
                    action_kwargs["config"] = config
                else:
                    response_message = easyucs.api_error_message
                    if not response_message:
                        response_message = "Failed to load config with UUID " + str(config_uuid)
                    response = response_handle(response=response_message, code=400)
                    return response

                inventory = load_object(object_type="inventory", object_uuid=inventory_uuid, device=device)
                if inventory:
                    action_kwargs["inventory"] = inventory
                else:
                    response = response_handle(response="Inventory not found with UUID: " + str(inventory_uuid),
                                               code=404)
                    return response

                try:
                    # We create a new task
                    if device.metadata.device_type in ["cimc"]:
                        task_uuid = easyucs.task_manager.add_task(
                            name="GenerateReportUcsImc", device_name=str(device.name), device_uuid=str(device.uuid),
                            config_uuid=str(config.uuid), inventory_uuid=str(inventory.uuid)
                        )
                    elif device.metadata.device_type in ["ucsm"]:
                        task_uuid = easyucs.task_manager.add_task(
                            name="GenerateReportUcsSystem", device_name=str(device.name), device_uuid=str(device.uuid),
                            config_uuid=str(config.uuid), inventory_uuid=str(inventory.uuid)
                        )
                    else:
                        response = response_handle(response="Unsupported device type", code=500)
                        return response

                    # We schedule the "generate report" action through the scheduler
                    pending_task = {
                        "task_uuid": task_uuid,
                        "action_type": "generate",
                        "object_type": "report",
                        "timeout": timeout_values["generate_report"],
                        "action_kwargs": action_kwargs
                    }
                    if not easyucs.task_manager.add_to_pending_tasks(pending_task):
                        response = response_handle(response="Error while scheduling the task. Task Queue might be Full."
                                                            " Try again after some time.", code=400)
                        return response

                except Exception as err:
                    response = response_handle(response="Error while generating report: " + str(err), code=500)
                    return response

                response = response_handle(response={"task": str(task_uuid)}, code=200)
            else:
                response = response_handle(response="Device not found with UUID: " + device_uuid, code=404)
        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/reports/<report_uuid>", methods=['GET', 'DELETE'])
# @cross_origin()
def device_uuid_report_uuid(device_uuid, report_uuid):
    if request.method == 'GET':
        try:
            filter_attribute = request.args.get("filter_attribute", None)
            filter_type = request.args.get("filter_type", None)
            filter_value = request.args.get("filter_value", None)

            if filter_attribute is None or filter_type is None or filter_value is None:
                filter = None
            else:
                filter = [filter_attribute, filter_type, filter_value]

            order_by_attribute = request.args.get("order_by_attribute", None)
            order_by_direction = request.args.get("order_by_direction", None)

            if order_by_attribute is None or order_by_direction is None:
                order_by = None
            else:
                order_by = (order_by_attribute, order_by_direction)

            page_size = request.args.get("page_size", None)
            page_number = int(request.args.get("page_number", 0))

            report_dict = get_object_from_db(object_type="report", uuid=report_uuid, filter=filter,
                                             page_size=page_size, page_number=page_number, order_by=order_by)

            if report_dict:
                response = response_handle(report_dict, 200)
            else:
                response = response_handle(code=500, response=f"Could not find report with UUID {report_uuid}")
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response

    if request.method == 'DELETE':
        try:
            report_metadata_list = easyucs.repository_manager.get_metadata(object_type="report", uuid=report_uuid)
            if len(report_metadata_list) == 1:
                if easyucs.repository_manager.delete_from_repository(metadata=report_metadata_list[0]):
                    response = response_handle(code=200)
                else:
                    response = response_handle(code=500, response="Could not delete report from repository")
            else:
                response = response_handle(response="Report with UUID " + report_uuid + " not found", code=404)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/reports/<report_uuid>/actions", methods=['GET'])
# @cross_origin()
def device_uuid_report_uuid_actions(device_uuid, report_uuid):
    if request.method == 'GET':
        try:
            device_metadata_list = easyucs.repository_manager.get_metadata(object_type="device", uuid=device_uuid)
            if len(device_metadata_list) == 1:
                device_type = device_metadata_list[0].device_type

                report_metadata_list = easyucs.repository_manager.get_metadata(object_type="report", uuid=report_uuid)
                if len(report_metadata_list) == 1:

                    actions = None
                    if device_type in ["cimc", "ucsm"]:
                        actions = ["download"]

                    actions_dict = {"actions": actions}

                    response = response_handle(actions_dict, 200)

                else:
                    response = response_handle(response="Report with UUID " + report_uuid + " not found", code=404)
            else:
                response = response_handle(response="Device with UUID " + device_uuid + " not found", code=404)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/devices/<device_uuid>/reports/<report_uuid>/actions/download", methods=['GET'])
# @cross_origin()
def device_uuid_report_uuid_actions_download(device_uuid, report_uuid):
    if request.method == 'GET':
        try:
            # Trying to find device using its metadata (DB)
            easyucs.logger(level="debug", message="Trying to find device with UUID " + device_uuid + " from the DB")
            device_metadata_list = easyucs.repository_manager.get_metadata(object_type="device", uuid=device_uuid)
            if len(device_metadata_list) != 1:
                response = response_handle(response="Device not found with UUID: " + device_uuid, code=404)
                return response

            # Trying to find report using its metadata (DB)
            easyucs.logger(level="debug",
                           message="Trying to find report with UUID " + report_uuid + " from the DB")
            report_metadata_list = easyucs.repository_manager.get_metadata(object_type="report", uuid=report_uuid)
            if len(report_metadata_list) == 1:
                file_path = os.path.abspath(os.path.join(EASYUCS_ROOT, report_metadata_list[0].file_path))
                response = send_file(file_path, as_attachment=True,
                                     download_name="report_" + report_metadata_list[0].report_type + "_" +
                                                   report_uuid + ".docx")
            else:
                response = response_handle(response="Report not found with UUID: " + report_uuid, code=404)

        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/inventories", methods=['GET'])
# @cross_origin()
def inventories():
    if request.method == 'GET':
        try:
            filter_attribute = request.args.get("filter_attribute", None)
            filter_type = request.args.get("filter_type", None)
            filter_value = request.args.get("filter_value", None)

            if filter_attribute is None or filter_type is None or filter_value is None:
                filter = None
            else:
                filter = [filter_attribute, filter_type, filter_value]

            order_by_attribute = request.args.get("order_by_attribute", None)
            order_by_direction = request.args.get("order_by_direction", None)

            if order_by_attribute is None or order_by_direction is None:
                order_by = None
            else:
                order_by = (order_by_attribute, order_by_direction)

            page_size = request.args.get("page_size", None)
            page_number = int(request.args.get("page_number", 0))

            inventory_dict = get_object_from_db(object_type="inventory", filter=filter, page_size=page_size,
                                                page_number=page_number, order_by=order_by)

            if inventory_dict:
                response = response_handle(inventory_dict, 200)
            else:
                response = response_handle(code=500,
                                           response="Unexpected error while fetching data from DB. Please check logs.")
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/notifications", methods=['GET', 'POST'])
def notifications():
    global notifs
    if request.method == 'GET':
        try:
            notif_dict = {"notifications": notifs}
            response = response_handle(notif_dict, 200)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response

    if request.method == 'POST':
        payload = request.json
        try:
            new_notifs = payload["notifications"]
            if not isinstance(new_notifs, int):
                response = response_handle(code=500, response="Impossible to save notification number: not an integer")
            else:
                notifs = new_notifs
                response = response_handle(code=200)
        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/logs/session", methods=['GET', 'POST'])
def logs():
    global logContent
    global displayLogsBool
    if request.method == 'GET':
        try:
            logs_dict = {"logs": logContent,
                         "displayLogsBool": displayLogsBool
                         }
            response = response_handle(logs_dict, 200)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response

    if request.method == 'POST':
        try:
            payload = request.json
            if not ("logs" in payload or "displayLogsBool" in payload):
                response = response_handle(code=500, response="Impossible to save logs: wrong payload provided")
                return response

            if "logs" in payload:
                logs = payload["logs"]
                if not isinstance(logs, str):
                    response = response_handle(code=500, response="Impossible to save logs string: not a string")
                    return response
                else:
                    logContent = logs
                    response = response_handle(code=200)

            if "displayLogsBool" in payload:
                logsBool = payload["displayLogsBool"]
                if not isinstance(logsBool, bool):
                    response = response_handle(code=500, response="Impossible to save display log bool: not a boolean")
                    return response
                else:
                    displayLogsBool = logsBool
                    response = response_handle(code=200)

        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/reports", methods=['GET'])
# @cross_origin()
def reports():
    if request.method == 'GET':
        try:
            filter_attribute = request.args.get("filter_attribute", None)
            filter_type = request.args.get("filter_type", None)
            filter_value = request.args.get("filter_value", None)

            if filter_attribute is None or filter_type is None or filter_value is None:
                filter = None
            else:
                filter = [filter_attribute, filter_type, filter_value]

            order_by_attribute = request.args.get("order_by_attribute", None)
            order_by_direction = request.args.get("order_by_direction", None)

            if order_by_attribute is None or order_by_direction is None:
                order_by = None
            else:
                order_by = (order_by_attribute, order_by_direction)

            page_size = request.args.get("page_size", None)
            page_number = int(request.args.get("page_number", 0))

            report_dict = get_object_from_db(object_type="report", filter=filter, page_size=page_size,
                                             page_number=page_number, order_by=order_by)

            if report_dict:
                response = response_handle(report_dict, 200)
            else:
                response = response_handle(code=500,
                                           response="Unexpected error while fetching data from DB. Please check logs.")
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/tasks", methods=['GET'])
# @cross_origin()
def tasks():
    if request.method == 'GET':
        try:
            filter_attribute = request.args.get("filter_attribute", None)
            filter_type = request.args.get("filter_type", None)
            filter_value = request.args.get("filter_value", None)

            if filter_attribute is None or filter_type is None or filter_value is None:
                filter = None
            else:
                filter = [filter_attribute, filter_type, filter_value]

            order_by_attribute = request.args.get("order_by_attribute", None)
            order_by_direction = request.args.get("order_by_direction", None)

            if order_by_attribute is None or order_by_direction is None:
                order_by = None
            else:
                order_by = (order_by_attribute, order_by_direction)

            page_size = request.args.get("page_size", None)
            page_number = int(request.args.get("page_number", 0))

            task_dict = get_object_from_db(object_type="task", filter=filter, page_size=page_size,
                                           page_number=page_number, order_by=order_by)

            if task_dict:
                response = response_handle(task_dict, 200)
            else:
                response = response_handle(code=500,
                                           response="Unexpected error while fetching data from DB. Please check logs.")
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/tasks/<task_uuid>", methods=['GET'])
# @cross_origin()
def task_uuid(task_uuid):
    if request.method == 'GET':
        try:
            filter_attribute = request.args.get("filter_attribute", None)
            filter_type = request.args.get("filter_type", None)
            filter_value = request.args.get("filter_value", None)

            if filter_attribute is None or filter_type is None or filter_value is None:
                filter = None
            else:
                filter = [filter_attribute, filter_type, filter_value]

            order_by_attribute = request.args.get("order_by_attribute", None)
            order_by_direction = request.args.get("order_by_direction", None)

            if order_by_attribute is None or order_by_direction is None:
                order_by = None
            else:
                order_by = (order_by_attribute, order_by_direction)

            page_size = request.args.get("page_size", None)
            page_number = int(request.args.get("page_number", 0))

            task_dict = get_object_from_db(object_type="task", uuid=task_uuid, filter=filter, page_size=page_size,
                                           page_number=page_number, order_by=order_by)
            steps_dict = get_object_from_db(object_type="taskstep", task_uuid=task_uuid, order_by=("order", "desc"))

            if task_dict:
                if steps_dict and steps_dict.get("steps", None):
                    task_dict["task"]["steps"] = steps_dict["steps"]
                    response = response_handle(task_dict, 200)
                else:
                    response = response_handle(code=500,
                                               response=f"Error collecting task steps for task UUID {task_uuid}")
            else:
                response = response_handle(code=500,
                                           response=f"Could not find task with UUID {task_uuid}")
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/tasks/<task_uuid>/actions", methods=['GET'])
# @cross_origin()
def task_uuid_actions(task_uuid):
    if request.method == 'GET':
        try:
            actions = ["cancel"]
            actions_dict = {"actions": actions}
            response = response_handle(actions_dict, 200)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/tasks/<task_uuid>/actions/cancel", methods=['POST'])
# @cross_origin()
def task_uuid_actions_cancel(task_uuid):
    if request.method == 'POST':
        try:
            task = easyucs.task_manager.find_task_by_uuid(uuid=task_uuid)
            if task:
                if task.metadata.status in ["in_progress"]:
                    task.cancel = True
                    retries = 0
                    while task.metadata.status not in ["failed", "successful"] and retries < 3:
                        time.sleep(1)
                        retries += 1
                    if retries >= 3 and task.metadata.status not in ["failed", "successful"]:
                        response = response_handle(response="Failed to cancel the task.", code=500)
                    else:
                        response = response_handle(code=200)
                elif task.metadata.status in ["pending"]:
                    easyucs.task_manager.stop_task(uuid=task_uuid, status="failed",
                                                   status_message="Task cancelled by user")
                    response = response_handle(code=200)
                else:
                    response = response_handle(
                        response="Task with UUID " + task_uuid + " is not pending or in progress", code=400)
            else:
                response = response_handle(response="Task with UUID " + task_uuid + " not found", code=404)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/easyucs/settings", methods=['GET', 'PUT'])
# @cross_origin()
def easyucs_settings():
    if request.method == 'GET':
        try:
            settings = copy.deepcopy(easyucs.repository_manager.settings)
            settings_dict = {"settings": settings}
            response = response_handle(response=settings_dict, code=200)
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response

    if request.method == 'PUT':
        try:
            payload = request.json
            if not validate_json(json_data=payload, schema_path="api/specs/easyucs_settings_put.json", logger=easyucs):
                response = response_handle(code=400, response="Invalid payload")
                return response

            settings = copy.deepcopy(easyucs.repository_manager.settings)
            for key in payload:
                if isinstance(payload[key], dict):
                    for field in payload[key]:
                            settings[key][field] = payload[key][field]
                else:
                    settings[key] = payload[key]

            # Updating the settings.json file
            with open("settings.json", "w") as settings_file:
                json.dump(settings, settings_file, indent=3)

            # Saving the updated settings.json in repository_manager.settings object
            easyucs.repository_manager._init_settings()

            # Returning the settings as response
            settings = copy.deepcopy(easyucs.repository_manager.settings)
            settings_dict = {"settings": settings}
            response = response_handle(response=settings_dict, code=200)
        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/easyucs/backup/actions/download", methods=['POST'])
# @cross_origin()
def easyucs_backup_actions_download():
    if request.method == 'POST':
        try:
            payload = request.json
            if not validate_json(json_data=payload, schema_path="api/specs/easyucs_backup_download_post.json",
                                 logger=easyucs):
                response = response_handle(code=400, response="Invalid payload")
                return response

            if easyucs.task_manager.is_any_task_in_progress():
                response = response_handle(code=400, response="Some tasks are in-progress/pending. Please cancel or "
                                                              "wait for them to end.")
                return response

            output_files = [
                {
                    "file_path": os.path.join(EASYUCS_ROOT, 'data', 'files'),
                    "file_name": "devices"
                },
                {
                    "file_path": os.path.join(EASYUCS_ROOT, 'data', 'keys'),
                    "file_name": "keys"
                },
                {
                    "file_path": os.path.join(EASYUCS_ROOT, 'settings.json'),
                    "file_name": "settings.json"
                },
                {
                    "file_path": easyucs.repository_manager.create_db_backup(decrypt_passwords=True),
                    "file_name": easyucs.repository_manager.REPOSITORY_DB_BACKUP_NAME
                }
            ]

            # We get an encryption key of length 4-32 character as an input. If the length is less than 32 characters,
            # then we append the string with multiple "\0" to make it 32 char long. Then we encode the key using
            # urlsafe base64 alphabets to make it Fernet compatible.
            key = base64.urlsafe_b64encode(payload["encryption_key"].encode("utf-8").ljust(32, b"\0"))

            # Create a Fernet object with the key
            fernet = Fernet(key)

            tar_file_name = "backup.tar.gz"
            bin_file_name = "backup.bin"

            def stream_file():
                """
                Function to send backup.bin file as a stream of response. After sending the files, we delete the files.
                """
                # Iterate over the files/folder which needs to be archived. And them to the tar file.
                tar_file_path = os.path.join(EASYUCS_ROOT, easyucs.repository_manager.REPOSITORY_FOLDER_NAME,
                                             easyucs.repository_manager.REPOSITORY_TMP_FOLDER_NAME, tar_file_name)
                easyucs.logger(level="debug", message=f"Creating the {tar_file_name} file for backing up everything")
                with tarfile.open(tar_file_path, "w|gz") as tar:
                    for each_file in output_files:
                        if os.path.exists(each_file["file_path"]):
                            tar.add(each_file["file_path"],
                                    arcname=os.path.relpath(path=each_file["file_path"], start=EASYUCS_ROOT))

                # Reading and encrypting the tar data and creating a ".bin" file out of it
                bin_file_path = os.path.join(EASYUCS_ROOT, easyucs.repository_manager.REPOSITORY_FOLDER_NAME,
                                             easyucs.repository_manager.REPOSITORY_TMP_FOLDER_NAME, bin_file_name)
                easyucs.logger(level="debug", message=f"Encrypting the {tar_file_name} file to {bin_file_name} file")
                with open(tar_file_path, "rb") as f_read, open(bin_file_path, "wb") as f_write:
                    binary_tar_data = f_read.read()
                    encrypted_data = encrypt(data=binary_tar_data, fernet=fernet, logger=easyucs)
                    if not encrypted_data:
                        error_message = easyucs.api_error_message
                        if not error_message:
                            error_message = "Failed to encrypt the backup tar.gz file. Check logs."
                        return response_handle(code=500, response=error_message)
                    f_write.write(encrypted_data)

                with open(bin_file_path, "rb") as f_read:
                    yield from f_read

                # We delete the saved backup file at the end after the response is streamed
                easyucs.logger(level="debug", message=f"Removing the files: {tar_file_path}, {bin_file_path} and "
                                                      f"{output_files[3]['file_path']}")
                os.remove(tar_file_path)
                os.remove(bin_file_path)
                os.remove(output_files[3]["file_path"])

            response = Response(stream_file(), mimetype="application/octet-stream")
            response.headers["Content-Disposition"] = "attachment; filename=%s" % bin_file_name
        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


@app.route("/easyucs/backup/actions/restore", methods=['POST'])
# @cross_origin()
def easyucs_backup_actions_restore():
    if request.method == 'POST':
        try:
            # We validate the form data using schema "easyucs_backup_download_post.json", because "request.form"
            # only contains attribute "encryption_key". So, we reuse the schema "easyucs_backup_download_post.json".
            if not validate_json(json_data=request.form, schema_path="api/specs/easyucs_backup_download_post.json",
                                 logger=easyucs):
                response = response_handle(code=400, response="Invalid payload")
                return response

            # Making sure that no tasks are in progress
            if easyucs.task_manager.is_any_task_in_progress():
                response = response_handle(code=400, response="Some tasks are in-progress/pending. Please cancel or "
                                                              "wait for them to end.")
                return response

            # We get an encryption key of length 4-32 character as an input. If the length is less than 32 characters,
            # then we append the string with multiple "\0" to make it 32 char long. Then we encode the key using
            # urlsafe base64 alphabets to make it Fernet compatible.
            key = base64.urlsafe_b64encode(request.form["encryption_key"].encode("utf-8").ljust(32, b"\0"))

            # Create a Fernet object with the key
            fernet = Fernet(key)

            # Object to store the backup file content
            bin_file = request.files['backup_file']

            # Decrypt the data using Fernet
            decrypted_data = decrypt(data=bin_file.read(), fernet=fernet, logger=easyucs)
            if not decrypted_data:
                error_message = easyucs.api_error_message
                if not error_message:
                    error_message = "Failed to decrypt the backup bin file. Check logs."
                return response_handle(code=500, response=error_message)

            db_backup_file_path = os.path.join(EASYUCS_ROOT, easyucs.repository_manager.REPOSITORY_FOLDER_NAME,
                                               easyucs.repository_manager.REPOSITORY_DB_FOLDER_NAME,
                                               easyucs.repository_manager.REPOSITORY_DB_BACKUP_NAME)

            tar_file_path = os.path.join(EASYUCS_ROOT, easyucs.repository_manager.REPOSITORY_FOLDER_NAME,
                                         easyucs.repository_manager.REPOSITORY_TMP_FOLDER_NAME,
                                         secure_filename("backup.tar.gz"))

            # Saving the backup file on disk.
            with open(tar_file_path, "wb") as tar_file:
                tar_file.write(decrypted_data)

            @after_this_request
            def cleanup(response):
                """
                Function to delete the saved files at the end of this request - whether it's successful or not
                """
                os.remove(tar_file_path)
                if os.path.exists(db_backup_file_path):
                    os.remove(db_backup_file_path)
                return response

            # If the backup is not a tar file then return error
            if not tarfile.is_tarfile(tar_file_path):
                response = response_handle(code=400, response="Invalid backup file. Please provide a valid backup file")
                return response

            # Steps for the restore:
            # 1. Extract the DB Backup file. Validate whether its compatible with current version.
            # 2. Delete the repository data (DB records and files).
            # 3. Extract rest of the files.
            # 4. Restore the key and settings file.
            # 5. Restore the DB Data.

            # Extract DB Backup file, to validate the backup file
            if not extract(tar_file=tar_file_path, logger=easyucs,
                           file_name=easyucs.repository_manager.REPOSITORY_DB_BACKUP_NAME):
                error_message = easyucs.api_error_message
                if not error_message:
                    error_message = "Failed to extract and decrypt the backup file. Check logs."
                return response_handle(code=400, response=error_message)

            # Read the contents of DB backup file and validate whether it could be applied to the current version
            with open(db_backup_file_path) as f:
                backup_data = json.load(f)
            backup_easyucs_version = backup_data["metadata"]["easyucs_version"]
            backup_easyucs_timestamp = backup_data["metadata"]["timestamp"]

            if backup_easyucs_version > __version__:
                easyucs.logger(level="error",
                               message=f"Cannot restore from higher EasyUCS version {backup_easyucs_version}")
                return False

            # Deleting the existing data from the DB
            easyucs.repository_manager.clear_db()

            # Deleting the existing files
            easyucs.repository_manager.clear_files()

            # Emptying the device_list and task list
            easyucs.device_manager.clear_device_list()
            easyucs.task_manager.clear_task_list()

            # Extract all the files from the tar
            if not extract(tar_file=tar_file_path, logger=easyucs):
                error_message = easyucs.api_error_message
                if not error_message:
                    error_message = "Failed to extract and decrypt the backup file. Check logs."
                return response_handle(code=400, response=error_message)

            # Restoring the key and settings from the backed up files
            if not easyucs.repository_manager.restore_key_and_settings_backup():
                return response_handle(code=400, response="Failed to restore Key and Settings. Check logs.")

            # Restore all the data from the DB backup
            easyucs.logger(level="info", message=f"Restoring DB from EasyUCS {backup_easyucs_version}, backed "
                                                 f"up on {backup_easyucs_timestamp}")
            if not easyucs.repository_manager.restore_db_backup(db_backup_data=backup_data):
                error_message = easyucs.api_error_message
                if not error_message:
                    error_message = "Failed to restore DB data. Check logs."
                return response_handle(code=400, response=error_message)

            response = response_handle(code=200)
        except BadRequest as err:
            response = response_handle(code=err.code, response=str(err.description))
        except Exception as err:
            response = response_handle(code=500, response=str(err))
        return response


def start(easyucs_object=None):
    global easyucs
    easyucs = easyucs_object
    # app.run(threaded=True)
