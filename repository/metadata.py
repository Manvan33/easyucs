# coding: utf-8
# !/usr/bin/env python

""" metadata.py: Easy UCS Deployment Tool """

import datetime
from repository.db.models import *


class GenericMetadata:
    def __init__(self, db_record=None, device_name=None, device_uuid=None, device_version=None, easyucs_version=None,
                 file_path=None, file_type=None, hash=None, is_hidden=False, is_system=False, name=None, origin=None,
                 parent=None, tags=[], system_usage=None, timestamp=None, uuid=None):
        self.db_record = db_record
        self.device_name = device_name
        self.device_uuid = device_uuid
        self.device_version = device_version
        self.easyucs_version = easyucs_version
        self.file_path = file_path
        self.file_type = file_type
        self.hash = hash
        self.is_hidden = is_hidden
        self.is_system = is_system
        self.name = name
        self.origin = origin
        self.parent = parent
        self.system_usage = system_usage
        self.tags = tags
        self.timestamp = timestamp
        self.uuid = uuid

        if not self.timestamp:
            self.timestamp = datetime.datetime.now()

        if self.parent:
            if not self.uuid:
                self.uuid = self.parent.uuid

            if hasattr(self.parent, "target"):
                # Parent is a GenericDevice
                device = self.parent
            elif hasattr(self.parent, "device"):
                # Parent is a GenericConfig, GenericInventory or GenericReport
                device = self.parent.device
                self.device_name = device.name
                # For UCS devices
                if hasattr(device.version, "version"):
                    self.device_version = device.version.version
                elif device.version is not None:
                    # For Intersight devices
                    if isinstance(device.version, str):
                        self.device_version = device.version

            self.device_uuid = device.uuid

    def __str__(self):
        return str(vars(self))


class BackupMetadata(GenericMetadata):
    TABLE_RECORD = BackupRecord
    OBJECT_TYPE = "backup"

    def __init__(self, backup_file_extension=None, backup_type=None, db_record=None, device_name=None, device_uuid=None,
                 device_version=None, easyucs_version=None, file_path=None, file_type="backup", hash=None,
                 is_hidden=False, is_system=False, name=None, origin=None, parent=None, system_usage=None, tags=[],
                 timestamp=None, uuid=None):
        GenericMetadata.__init__(self, db_record=db_record, device_name=device_name, device_uuid=device_uuid,
                                 device_version=device_version, easyucs_version=easyucs_version, file_path=file_path,
                                 file_type=file_type, hash=hash, is_hidden=is_hidden, is_system=is_system, name=name,
                                 origin=origin, parent=parent, system_usage=system_usage, tags=tags,
                                 timestamp=timestamp, uuid=uuid)

        self.backup_file_extension = backup_file_extension
        self.backup_type = backup_type


class ConfigMetadata(GenericMetadata):
    TABLE_RECORD = ConfigRecord
    OBJECT_TYPE = "config"

    def __init__(self, category=None, db_record=None, device_name=None, device_uuid=None, device_version=None,
                 easyucs_version=None, file_path=None, file_type="config", hash=None, is_hidden=False, is_system=False,
                 name=None, origin=None, parent=None, revision=None, source_config_uuid=None, source_device_uuid=None,
                 source_inventory_uuid=None, subcategory=None, system_usage=None, tags=[], timestamp=None, url=None,
                 uuid=None):
        GenericMetadata.__init__(self, db_record=db_record, device_name=device_name, device_uuid=device_uuid,
                                 device_version=device_version, easyucs_version=easyucs_version, file_path=file_path,
                                 file_type=file_type, hash=hash, is_hidden=is_hidden, is_system=is_system, name=name,
                                 origin=origin, parent=parent, system_usage=system_usage, tags=tags,
                                 timestamp=timestamp, uuid=uuid)
        self.category = category
        self.revision = revision
        self.source_config_uuid = source_config_uuid
        self.source_device_uuid = source_device_uuid
        self.source_inventory_uuid = source_inventory_uuid
        self.subcategory = subcategory
        self.url = url


class DeviceMetadata(GenericMetadata):
    TABLE_RECORD = DeviceRecord
    OBJECT_TYPE = "device"

    def __init__(self, backups_path=None, bypass_version_checks=False, cached_orgs=None, cache_path=None,
                 db_record=None, device_connector_claim_status=None, device_name=None,
                 device_connector_ownership_name=None, device_connector_ownership_user=None, device_type="generic",
                 device_type_long="Generic", device_uuid=None, device_version=None, easyucs_version=None,
                 file_path=None, file_type="device", hash=None, intersight_device_uuid=None, images_path=None,
                 is_hidden=False, is_reachable=False, is_system=False, key_id=None, name=None, origin=None,
                 parent=None, password=None, private_key_path=None, system_usage=None, tags=[], target=None,
                 timestamp=None, timestamp_last_connected=None, username=None, uuid=None):
        GenericMetadata.__init__(self, db_record=db_record, device_name=device_name, device_uuid=device_uuid,
                                 device_version=device_version, easyucs_version=easyucs_version, file_path=file_path,
                                 file_type=file_type, hash=hash, is_hidden=is_hidden, is_system=is_system, name=name,
                                 origin=origin, parent=parent, system_usage=system_usage, tags=tags,
                                 timestamp=timestamp, uuid=uuid)
        self.backups_path = backups_path
        self.bypass_version_checks = bypass_version_checks
        self.cached_orgs = cached_orgs
        self.cache_path = cache_path
        self.device_connector_claim_status = device_connector_claim_status
        self.device_connector_ownership_name = device_connector_ownership_name
        self.device_connector_ownership_user = device_connector_ownership_user
        self.device_type = device_type
        self.device_type_long = device_type_long
        self.images_path = images_path
        self.intersight_device_uuid = intersight_device_uuid
        self.is_reachable = is_reachable
        self.key_id = key_id
        self.password = password
        self.private_key_path = private_key_path
        self.target = target
        self.timestamp_last_connected = timestamp_last_connected
        self.username = username

        if self.parent:
            if not self.key_id:
                if hasattr(self.parent, "key_id"):
                    self.key_id = self.parent.key_id
            if not self.password:
                self.password = self.parent.password
            if not self.private_key_path:
                if hasattr(self.parent, "private_key_path"):
                    self.private_key_path = self.parent.private_key_path
            if not self.target:
                self.target = self.parent.target
            if not self.username:
                self.username = self.parent.username


class InventoryMetadata(GenericMetadata):
    TABLE_RECORD = InventoryRecord
    OBJECT_TYPE = "inventory"

    def __init__(self, db_record=None, device_name=None, device_uuid=None, device_version=None, easyucs_version=None,
                 file_path=None, file_type="inventory", hash=None, is_hidden=False, is_system=False, name=None,
                 origin=None, parent=None, system_usage=None, tags=[], timestamp=None, uuid=None):
        GenericMetadata.__init__(self, db_record=db_record, device_name=device_name, device_uuid=device_uuid,
                                 device_version=device_version, easyucs_version=easyucs_version, file_path=file_path,
                                 file_type=file_type, hash=hash, is_hidden=is_hidden, is_system=is_system, name=name,
                                 origin=origin, parent=parent, system_usage=system_usage, tags=tags,
                                 timestamp=timestamp, uuid=uuid)


class ReportMetadata(GenericMetadata):
    TABLE_RECORD = ReportRecord
    OBJECT_TYPE = "report"

    def __init__(self, config_uuid=None, db_record=None, device_name=None, device_uuid=None, device_version=None,
                 easyucs_version=None, file_path=None, file_type="report", hash=None, is_hidden=False, is_system=False,
                 inventory_uuid=None, language=None, name=None, origin=None, output_format=None, page_layout=None,
                 parent=None, report_type=None, size=None, system_usage=None, tags=[], target_device_uuid=None,
                 timestamp=None, uuid=None):
        GenericMetadata.__init__(self, db_record=db_record, device_name=device_name, device_uuid=device_uuid,
                                 device_version=device_version, easyucs_version=easyucs_version, file_path=file_path,
                                 file_type=file_type, hash=hash, is_hidden=is_hidden, is_system=is_system, name=name,
                                 origin=origin, parent=parent, system_usage=system_usage, tags=tags,
                                 timestamp=timestamp, uuid=uuid)
        self.config_uuid = config_uuid
        self.inventory_uuid = inventory_uuid
        self.language = language
        self.output_format = output_format
        self.page_layout = page_layout
        self.report_type = report_type
        self.size = size
        self.target_device_uuid = target_device_uuid


class GenericTaskMetadata:
    def __init__(self, db_record=None, description=None, easyucs_version=None, name=None, parent=None, status=None,
                 status_message=None, timestamp=None, timestamp_start=None, timestamp_stop=None, uuid=None):
        self.db_record = db_record
        self.description = description
        self.easyucs_version = easyucs_version
        self.name = name
        self.parent = parent
        self.status = status
        self.status_message = status_message
        self.timestamp = timestamp
        self.timestamp_start = timestamp_start
        self.timestamp_stop = timestamp_stop
        self.uuid = uuid

        if not self.timestamp:
            self.timestamp = datetime.datetime.now()

        if self.parent:
            if not self.uuid:
                self.uuid = self.parent.uuid

    def __str__(self):
        return str(vars(self))


class TaskMetadata(GenericTaskMetadata):
    TABLE_RECORD = TaskRecord
    OBJECT_TYPE = "task"

    def __init__(self, db_record=None, description=None, config_uuid=None, device_name=None, device_uuid=None,
                 easyucs_version=None,  inventory_uuid=None, name=None, parent=None, progress=None, report_uuid=None,
                 status="pending", status_message=None, target_device_uuid=None, timestamp=None, timestamp_start=None,
                 timestamp_stop=None, uuid=None):
        GenericTaskMetadata.__init__(self, db_record=db_record, description=description,
                                     easyucs_version=easyucs_version, name=name, parent=parent, status=status,
                                     status_message=status_message, timestamp=timestamp,
                                     timestamp_start=timestamp_start, timestamp_stop=timestamp_stop, uuid=uuid)
        self.config_uuid = config_uuid
        self.device_name = device_name
        self.device_uuid = device_uuid
        self.inventory_uuid = inventory_uuid
        self.report_uuid = report_uuid
        self.target_device_uuid = target_device_uuid
        self.progress = progress


class TaskStepMetadata(GenericTaskMetadata):
    TABLE_RECORD = TaskStepRecord
    OBJECT_TYPE = "taskstep"

    def __init__(self, db_record=None, description=None, easyucs_version=None, name=None, optional=False, order=None,
                 parent=None, status=None, status_message=None, task_uuid=None, timestamp=None, timestamp_start=None,
                 timestamp_stop=None, uuid=None, weight=None):
        GenericTaskMetadata.__init__(self, db_record=db_record, description=description,
                                     easyucs_version=easyucs_version, name=name, parent=parent, status=status,
                                     status_message=status_message, timestamp=timestamp,
                                     timestamp_start=timestamp_start, timestamp_stop=timestamp_stop, uuid=uuid)
        self.optional = optional
        self.order = order
        self.task_uuid = task_uuid
        self.weight = weight
