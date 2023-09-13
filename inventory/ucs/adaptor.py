# coding: utf-8
# !/usr/bin/env python

""" adaptor.py: Easy UCS Deployment Tool """

from common import read_json_file
from inventory.ucs.object import GenericUcsInventoryObject, UcsImcInventoryObject, UcsSystemInventoryObject
from inventory.ucs.port import UcsImcAdaptorPort, UcsImcNetworkAdapterPort, UcsSystemAdaptorPort, \
    UcsSystemNicAdaptorPort


class UcsAdaptor(GenericUcsInventoryObject):
    def __init__(self, parent=None, adaptor_unit=None):
        GenericUcsInventoryObject.__init__(self, parent=parent, ucs_sdk_object=adaptor_unit)

        self.model = self.get_attribute(ucs_sdk_object=adaptor_unit, attribute_name="model")

        self.ports = self._get_ports()

    def _get_imm_compatibility(self):
        """
        Returns adaptor IMM Compatibility from EasyUCS catalog files
        """
        if self.sku is not None:
            # We use the catalog file to get the adaptor IMM Compatibility
            adaptor_catalog = read_json_file(file_path="catalog/adaptors/" + self.sku + ".json", logger=self)
            if not adaptor_catalog:
                # If we are on a S3260 chassis, we look for the catalog file in the io_modules folder
                adaptor_catalog = read_json_file(file_path="catalog/io_modules/" + self.sku + ".json", logger=self)

            if adaptor_catalog:
                if "imm_compatible" in adaptor_catalog:
                    return adaptor_catalog["imm_compatible"]

        return None

    def _get_model_short_name(self):
        """
        Returns adaptor short name from EasyUCS catalog files
        """
        if self.sku is not None:
            # We use the catalog file to get the adaptor short name
            adaptor_catalog = read_json_file(file_path="catalog/adaptors/" + self.sku + ".json", logger=self)
            if not adaptor_catalog:
                # If we are on a S3260 chassis, we look for the catalog file in the io_modules folder
                adaptor_catalog = read_json_file(file_path="catalog/io_modules/" + self.sku + ".json", logger=self)

            if adaptor_catalog:
                if "model_short_name" in adaptor_catalog:
                    return adaptor_catalog["model_short_name"]

        return None

    def _get_ports(self):
        return []

    def _determine_adaptor_sku_and_type(self, vendor="", device="", subvendor="", subdevice=""):
        if vendor and device and subvendor and subdevice:
            # Broadcom
            if (vendor, device, subvendor, subdevice) == ("0x14e4", "0x1639", "0x14e4", "0x1639"):
                self.sku = "N2XX-ABPCI01"
            elif (vendor, device, subvendor, subdevice) == ("0x14e4", "0x164f", "0x14e4", "0x4113"):
                self.sku = "N2XX-ABPCI02"
            elif (vendor, device, subvendor, subdevice) == ("0x14e4", "0x1639", "0x14e4", "0x0906"):
                self.sku = "N2XX-ABPCI03"
            elif (vendor, device, subvendor, subdevice) == ("0x14e4", "0x1662", "0x1137", "0x0087"):
                self.sku = "UCSC-PCIE-BSFP"
            elif (vendor, device, subvendor, subdevice) == ("0x14e4", "0x1662", "0x1137", "0x007c"):
                self.sku = "UCSX-MLOM-001"
            elif (vendor, device, subvendor, subdevice) == ("0x14e4", "0x1662", "0x1137", "0x0088"):
                self.sku = "UCSC-PCIE-BTG"
            elif (vendor, device, subvendor, subdevice) == ("0x14e4", "0x16ae", "0x1137", "0x00cb"):
                self.sku = "UCSC-PCIE-B3SFP"
            # Cisco
            elif (vendor, device, subvendor, subdevice) == ("0x1137", "0x0042", "0x1137", "0x0047"):
                self.sku = "N2XX-ACPCI01"
            # Emulex
            elif (vendor, device, subvendor, subdevice) == ("0x19a2", "0x0700", "0x10df", "0xe602"):
                self.sku = "N2XX-AEPCI01"
            elif (vendor, device, subvendor, subdevice) == ("0x10df", "0xfe00", "0x10df", "0xfe00"):
                self.sku = "N2XX-AEPCI03"
            elif (vendor, device, subvendor, subdevice) == ("0x10df", "0xf100", "0x10df", "0xf100"):
                self.sku = "N2XX-AEPCI05"
            elif (vendor, device, subvendor, subdevice) == ("0x19a2", "0x0710", "0x10df", "0xe702"):
                self.sku = "UCSC-PCIE-ESFP"
            elif (vendor, device, subvendor, subdevice) == ("0x10df", "0xe200", "0x10df", "0xe280"):
                self.sku = "UCSC-PCIE-E16002"
            elif (vendor, device, subvendor, subdevice) == ("0x10df", "0x0720", "0x10df", "0xe800"):
                self.sku = "UCSC-PCIE-E14102"
            elif (vendor, device, subvendor, subdevice) == ("0x10df", "0x0720", "0x10df", "0xe867"):
                self.sku = "UCSC-PCIE-E14102B"
            elif (vendor, device, subvendor, subdevice) == ("0x10df", "0xe300", "0x10df", "0xe310"):
                self.sku = "UCSC-PCIE-BD16GF"
            elif (vendor, device, subvendor, subdevice) == ("0x10df", "0xe300", "0x10df", "0xe301"):
                self.sku = "UCSC-PCIE-BS32GF"
            elif (vendor, device, subvendor, subdevice) == ("0x10df", "0xe300", "0x10df", "0xe300"):
                self.sku = "UCSC-PCIE-BD32GF"
            # Intel
            elif (vendor, device, subvendor, subdevice) == ("0x8086", "0x10fb", "0x8086", "0x000c"):
                self.sku = "N2XX-AIPCI01"
            elif (vendor, device, subvendor, subdevice) == ("0x8086", "0x10e8", "0x8086", "0xa02b"):
                self.sku = "N2XX-AIPCI02"
            elif (vendor, device, subvendor, subdevice) == ("0x8086", "0x1521", "0x1137", "0x00b9"):
                self.sku = "UCSC-PCIE-IRJ45"
            elif (vendor, device, subvendor, subdevice) == ("0x8086", "0x1521", "0x1137", "0x023e"):
                self.sku = "UCSC-PCIE-IRJ45"
            elif (vendor, device, subvendor, subdevice) == ("0x8086", "0x1521", "0x1137", "0x0133"):
                self.sku = "UCSC-MLOM-IRJ45"
            elif (vendor, device, subvendor, subdevice) == ("0x8086", "0x1521", "0x1137", "0x00d3"):
                self.sku = None  # Onboard 1G LoM
                self.type = "nic"
            elif (vendor, device, subvendor, subdevice) == ("0x8086", "0x1528", "0x1137", "0x00d4"):
                self.sku = None  # Onboard 10G LoM
                self.type = "nic"
            elif (vendor, device, subvendor, subdevice) == ("0x8086", "0x1528", "0x1137", "0x00bf"):
                self.sku = "UCSC-PCIE-ITG"
            elif (vendor, device, subvendor, subdevice) == ("0x8086", "0x1563", "0x1137", "0x01a2"):
                self.sku = "UCSC-PCIE-ID10GC"
            elif (vendor, device, subvendor, subdevice) == ("0x8086", "0x1572", "0x1137", "0x020a"):
                self.sku = "UCSC-PCIE-ID10GF"
            elif (vendor, device, subvendor, subdevice) == ("0x8086", "0x1572", "0x1137", "0x013b"):
                self.sku = "UCSC-PCIE-IQ10GF"
            elif (vendor, device, subvendor, subdevice) == ("0x8086", "0x1589", "0x1137", "0x020b"):
                self.sku = "UCSC-PCIE-IQ10GC"
            elif (vendor, device, subvendor, subdevice) == ("0x8086", "0x1583", "0x1137", "0x013c"):
                self.sku = "UCSC-PCIE-ID40GF"
            elif (vendor, device, subvendor, subdevice) == ("0x8086", "0x158b", "0x1137", "0x0225"):
                self.sku = "UCSC-PCIE-ID25GF"
            # Mellanox
            elif (vendor, device, subvendor, subdevice) == ("0x15b3", "0x6750", "0x15b3", "0x0016"):
                self.sku = "N2XX-AMPCI01"
            # QLogic
            elif (vendor, device, subvendor, subdevice) == ("0x1077", "0x8000", "0x1077", "0x017e"):
                self.sku = "N2XX-AQPCI01"
            elif (vendor, device, subvendor, subdevice) == ("0x1077", "0x2432", "0x1077", "0x0138"):
                self.sku = "N2XX-AQPCI03"
            elif (vendor, device, subvendor, subdevice) == ("0x1077", "0x2532", "0x1077", "0x015d"):
                self.sku = "N2XX-AQPCI05"
            elif (vendor, device, subvendor, subdevice) == ("0x1077", "0x8020", "0x1077", "0x0207"):
                self.sku = "UCSC-PCIE-QSFP"
            elif (vendor, device, subvendor, subdevice) == ("0x1077", "0x8030", "0x1077", "0x0274"):
                self.sku = "UCSC-PCIE-Q2672"
            elif (vendor, device, subvendor, subdevice) == ("0x1077", "0x8030", "0x1077", "0x0271"):
                self.sku = "UCSC-PCIE-Q8362"
            elif (vendor, device, subvendor, subdevice) == ("0x14e4", "0x161a", "0x1137", "0x0153"):
                self.sku = "UCSC-PCIE-QNICBT"
            elif (vendor, device, subvendor, subdevice) == ("0x14e4", "0x161a", "0x1137", "0x0152"):
                self.sku = "UCSC-PCIE-QNICSFP"
            elif (vendor, device, subvendor, subdevice) == ("0x1077", "0x2261", "0x1077", "0x02c7"):
                self.sku = "UCSC-PCIE-QD16GF"
            elif (vendor, device, subvendor, subdevice) == ("0x1077", "0x2261", "0x1077", "0x02b6"):
                self.sku = "UCSC-PCIE-QD32GF"
            elif (vendor, device, subvendor, subdevice) == ("0x1077", "0x8070", "0x1137", "0x0246"):
                self.sku = "UCSC-PCIE-QD25GF"
            elif (vendor, device, subvendor, subdevice) == ("0x1077", "0x1634", "0x1137", "0x0245"):
                self.sku = "UCSC-PCIE-QD40GF"
            elif (vendor, device, subvendor, subdevice) == ("0x1077", "0x8070", "0x1077", "0x0019"):
                self.sku = "UCSC-OCP-QD25GF"

        if self.sku in ["N2XX-AEPCI01", "UCSC-PCIE-ESFP", "UCSC-PCIE-E14102B", "UCSC-PCIE-E14102", "N2XX-AQPCI01",
                        "UCSC-PCIE-QSFP", "UCSC-PCIE-Q8362"]:
            self.type = "cna"
        elif self.sku in ["N2XX-AEPCI03", "N2XX-AEPCI05", "UCSC-PCIE-E16002", "UCSC-PCIE-BD16GF",
                          "UCSC-PCIE-BS32GF", "UCSC-PCIE-BD32GF", "N2XX-AQPCI03", "N2XX-AQPCI05",
                          "UCSC-PCIE-Q2672", "UCSC-PCIE-QD16GF", "UCSC-PCIE-QD32GF"]:
            self.type = "hba"
        elif self.sku in ["N2XX-ABPCI01", "N2XX-ABPCI01-M3", "N2XX-ABPCI02", "N2XX-ABPCI03", "N2XX-ABPCI03-M3",
                          "UCSC-PCIE-BSFP", "UCSC-PCIE-B3SFP", "UCSC-PCIE-BTG", "N2XX-AIPCI01", "N2XX-AIPCI02",
                          "UCSC-PCIE-IRJ45", "UCSC-MLOM-IRJ45", "UCSC-PCIE-ITG", "UCSC-PCIE-ID10GC",
                          "UCSC-PCIE-ID10GF", "UCSC-PCIE-IQ10GF", "UCSC-PCIE-IQ10GC", "UCSC-PCIE-ID40GF",
                          "UCSC-PCIE-ID25GF", "N2XX-AMPCI01", "UCSC-PCIE-QNICBT", "UCSC-PCIE-QNICSFP",
                          "UCSC-PCIE-QD25GF", "UCSC-PCIE-QD40GF", "UCSX-MLOM-001", "UCSC-OCP-QD10GC",
                          "UCSC-OCP-QD25GF"]:
            self.type = "nic"
        elif self.sku in ["N2XX-ACPCI01", "UCSC-PCIE-CSC-02", "UCSC-PCIE-C10T-02", "UCSC-MLOM-CSC-02",
                          "UCSC-MLOM-C10T-02", "UCSC-PCIE-C40Q-02", "UCSC-PCIE-C40Q-03", "UCSC-MLOM-C40Q-03",
                          "UCSC-PCIE-C25Q-04", "UCSC-MLOM-C25Q-04", "UCSC-M-V25-04", "UCSC-M-V100-04",
                          "UCSC-PCIE-C100-04", "UCSC-MLOM-C100-04", "UCSC-P-V5Q50G", "UCSC-M-V5Q50G", "UCSC-P-V5D200G",
                          "UCSC-M-V5D200G"]:
            self.type = "vic"
        elif self.pci_slot == "L":  # For LoM ports
            self.type = "nic"
        elif self.pci_slot == "14" and self._parent.sku == "UCS-S3260-M5SRB":  # For LoM on S3260 M5 server node
            self.type = "nic"


class UcsSystemAdaptor(UcsAdaptor, UcsSystemInventoryObject):
    _UCS_SDK_OBJECT_NAME = "adaptorUnit"
    _UCS_SDK_CATALOG_OBJECT_NAME = "adaptorFruCapProvider"
    _UCS_SDK_FIRMWARE_RUNNING_SUFFIX = "/mgmt/fw-system"

    def __init__(self, parent=None, adaptor_unit=None):
        UcsAdaptor.__init__(self, parent=parent, adaptor_unit=adaptor_unit)

        self.blade_id = self.get_attribute(ucs_sdk_object=adaptor_unit, attribute_name="blade_id")
        self.id = self.get_attribute(ucs_sdk_object=adaptor_unit, attribute_name="id")
        self.pci_slot = self.get_attribute(ucs_sdk_object=adaptor_unit, attribute_name="pci_slot")
        self.revision = self.get_attribute(ucs_sdk_object=adaptor_unit, attribute_name="revision")
        self.serial = self.get_attribute(ucs_sdk_object=adaptor_unit, attribute_name="serial")
        self.vendor = self.get_attribute(ucs_sdk_object=adaptor_unit, attribute_name="vendor")

        UcsSystemInventoryObject.__init__(self, parent=parent, ucs_sdk_object=adaptor_unit)

        self.imm_compatible = None
        self.short_name = None
        if self._inventory.load_from == "live":
            self.driver_name_ethernet = None
            self.driver_name_fibre_channel = None
            self.driver_version_ethernet = None
            self.driver_version_fibre_channel = None
            self.short_name = self._get_model_short_name()
            self.imm_compatible = self._get_imm_compatibility()
        elif self._inventory.load_from == "file":
            for attribute in ["driver_name_ethernet", "driver_name_fibre_channel", "driver_version_ethernet",
                              "driver_version_fibre_channel", "imm_compatible", "short_name"]:
                setattr(self, attribute, None)
                if attribute in adaptor_unit:
                    setattr(self, attribute, self.get_attribute(ucs_sdk_object=adaptor_unit, attribute_name=attribute))

    def _get_ports(self):
        if self._inventory.load_from == "live":
            ports = self._inventory.get_inventory_objects_under_dn(dn=self.dn, object_class=UcsSystemAdaptorPort,
                                                                   parent=self)
            if not ports:
                # In case the ports are not VIC ports, we try with NIC ports instead
                ports = self._inventory.get_inventory_objects_under_dn(dn=self.dn, object_class=UcsSystemNicAdaptorPort,
                                                                       parent=self)
            return ports
        elif self._inventory.load_from == "file" and "ports" in self._ucs_sdk_object:
            return [UcsSystemAdaptorPort(self, port) for port in self._ucs_sdk_object["ports"]]
        else:
            return []


class UcsSystemAdaptorPortExpander(UcsAdaptor, UcsSystemInventoryObject):
    _UCS_SDK_OBJECT_NAME = "adaptorUnitExtn"
    _UCS_SDK_CATALOG_OBJECT_NAME = "adaptorFruCapProvider"

    def __init__(self, parent=None, adaptor_unit_extn=None):
        UcsAdaptor.__init__(self, parent=parent, adaptor_unit=adaptor_unit_extn)

        self.id = self.get_attribute(ucs_sdk_object=adaptor_unit_extn, attribute_name="id")
        self.revision = self.get_attribute(ucs_sdk_object=adaptor_unit_extn, attribute_name="revision")
        self.serial = self.get_attribute(ucs_sdk_object=adaptor_unit_extn, attribute_name="serial")
        self.vendor = self.get_attribute(ucs_sdk_object=adaptor_unit_extn, attribute_name="vendor")

        UcsSystemInventoryObject.__init__(self, parent=parent, ucs_sdk_object=adaptor_unit_extn)

        self.imm_compatible = None
        self.short_name = None
        if self._inventory.load_from == "live":
            self.short_name = self._get_model_short_name()
            self.imm_compatible = self._get_imm_compatibility()
        elif self._inventory.load_from == "file":
            for attribute in ["imm_compatible", "short_name"]:
                setattr(self, attribute, None)
                if attribute in adaptor_unit_extn:
                    setattr(self, attribute, self.get_attribute(ucs_sdk_object=adaptor_unit_extn,
                                                                attribute_name=attribute))


class UcsImcAdaptor(UcsAdaptor, UcsImcInventoryObject):
    _UCS_SDK_OBJECT_NAME = "adaptorUnit"
    _UCS_SDK_CATALOG_OBJECT_NAME = "pidCatalogPCIAdapter"
    _UCS_SDK_CATALOG_IDENTIFY_ATTRIBUTE = "slot"
    _UCS_SDK_OBJECT_IDENTIFY_ATTRIBUTE = "pci_slot"

    def __init__(self, parent=None, adaptor_unit=None):
        UcsAdaptor.__init__(self, parent=parent, adaptor_unit=adaptor_unit)

        self.id = self.get_attribute(ucs_sdk_object=adaptor_unit, attribute_name="id")
        self.pci_slot = self.get_attribute(ucs_sdk_object=adaptor_unit, attribute_name="pci_slot")
        self.serial = self.get_attribute(ucs_sdk_object=adaptor_unit, attribute_name="serial")
        self.type = "vic"
        self.vendor = self.get_attribute(ucs_sdk_object=adaptor_unit, attribute_name="vendor")

        UcsImcInventoryObject.__init__(self, parent=parent, ucs_sdk_object=adaptor_unit)

        # Small fix for when Adaptor VIC is not present in UCS IMC catalog
        if hasattr(self, "sku"):
            if not self.sku:
                if self.model in ["N2XX-ACPCI01", "UCSC-PCIE-CSC-02", "UCSC-PCIE-C10T-02", "UCSC-MLOM-CSC-02",
                                  "UCSC-MLOM-C10T-02", "UCSC-PCIE-C40Q-02", "UCSC-PCIE-C40Q-03", "UCSC-MLOM-C40Q-03",
                                  "UCSC-PCIE-C25Q-04", "UCSC-MLOM-C25Q-04", "UCSC-M-V25-04", "UCSC-M-V100-04",
                                  "UCSC-PCIE-C100-04", "UCSC-MLOM-C100-04", "UCSC-P-V5Q50G", "UCSC-M-V5Q50G",
                                  "UCSC-P-V5D200G", "UCSC-M-V5D200G"]:
                    self.sku = self.model

        self.short_name = None
        if self._inventory.load_from == "live":
            if not self._find_pci_details():
                self.option_rom_status = None
                self.version = None
            self.short_name = self._get_model_short_name()
        elif self._inventory.load_from == "file":
            for attribute in ["option_rom_status", "short_name", "version"]:
                setattr(self, attribute, None)
                if attribute in adaptor_unit:
                    setattr(self, attribute, self.get_attribute(ucs_sdk_object=adaptor_unit, attribute_name=attribute))

    def _find_pci_details(self):
        # We check if we already have fetched the list of pciEquipSlot objects
        if self._inventory.sdk_objects["pciEquipSlot"] is not None:
            pci_equip_slot_list = [pci_equip_slot for pci_equip_slot in self._inventory.sdk_objects["pciEquipSlot"]
                                   if "sys/rack-unit-" + self._parent.id + "/" in pci_equip_slot.dn
                                   and self.pci_slot == pci_equip_slot.id]

            # We fetch the pciEquipSlot details if there is one and only one pciEquipSlot object in the list
            if len(pci_equip_slot_list) == 1:
                self.option_rom_status = pci_equip_slot_list[0].option_rom_status
                self.version = pci_equip_slot_list[0].version
                return True

        return False

    def _get_ports(self):
        if self._inventory.load_from == "live":
            return self._inventory.get_inventory_objects_under_dn(dn=self.dn, object_class=UcsImcAdaptorPort,
                                                                  parent=self)
        elif self._inventory.load_from == "file" and "ports" in self._ucs_sdk_object:
            return [UcsImcAdaptorPort(self, port) for port in self._ucs_sdk_object["ports"]]
        else:
            return []


class UcsImcNetworkAdapter(UcsAdaptor, UcsImcInventoryObject):
    _UCS_SDK_OBJECT_NAME = "networkAdapterUnit"
    _UCS_SDK_CATALOG_OBJECT_NAME = "pidCatalogPCIAdapter"
    _UCS_SDK_CATALOG_IDENTIFY_ATTRIBUTE = "slot"
    _UCS_SDK_OBJECT_IDENTIFY_ATTRIBUTE = "pci_slot"

    def __init__(self, parent=None, network_adapter_unit=None):
        UcsAdaptor.__init__(self, parent=parent, adaptor_unit=network_adapter_unit)

        self.pci_slot = self.get_attribute(ucs_sdk_object=network_adapter_unit, attribute_name="slot",
                                           attribute_secondary_name="pci_slot")
        self.num_intf = self.get_attribute(ucs_sdk_object=network_adapter_unit, attribute_name="num_intf")

        UcsImcInventoryObject.__init__(self, parent=parent, ucs_sdk_object=network_adapter_unit)

        self.id = None
        self.short_name = None
        if self._inventory.load_from == "live":
            self.id = self.pci_slot
            if not self._find_pci_details():
                self.option_rom_status = None
                self.vendor = None
                self.version = None

            # We default the interface type to "unknown"
            self.type = "unknown"

            # Handling potentially incomplete UCS catalog entries (when SKU is not found)
            if self.sku is None:
                # We do not perform this operation for S3260 server node LoM ports since there is no PID for those
                if self.pci_slot not in ["SBLoM1"]:
                    if hasattr(self, "_pid_catalog"):
                        if all(hasattr(self._pid_catalog, attr) for attr in ["vendor", "device", "subvendor",
                                                                             "subdevice"]):
                            vendor = self._pid_catalog.vendor
                            device = self._pid_catalog.device
                            subvendor = self._pid_catalog.subvendor
                            subdevice = self._pid_catalog.subdevice

                            self._determine_adaptor_sku_and_type(vendor=vendor, device=device, subvendor=subvendor,
                                                                 subdevice=subdevice)
            else:
                if self.type == "unknown":
                    self._determine_adaptor_sku_and_type()

            self.short_name = self._get_model_short_name()

        elif self._inventory.load_from == "file":
            for attribute in ["id", "option_rom_status", "short_name", "type", "vendor", "version"]:
                setattr(self, attribute, None)
                if attribute in network_adapter_unit:
                    setattr(self, attribute, self.get_attribute(ucs_sdk_object=network_adapter_unit,
                                                                attribute_name=attribute))

    def _find_pci_details(self):
        # We check if we already have fetched the list of pciEquipSlot objects
        if self._inventory.sdk_objects["pciEquipSlot"] is not None:
            pci_equip_slot_list = [pci_equip_slot for pci_equip_slot in self._inventory.sdk_objects["pciEquipSlot"]
                                   if "sys/rack-unit-" + self._parent.id + "/" in pci_equip_slot.dn
                                   and self.pci_slot == pci_equip_slot.id]

            # We fetch the pciEquipSlot details if there is one and only one pciEquipSlot object in the list
            if len(pci_equip_slot_list) == 1:
                self.option_rom_status = pci_equip_slot_list[0].option_rom_status
                self.vendor = pci_equip_slot_list[0].vendor
                self.version = pci_equip_slot_list[0].version
                return True

        return False

    def _get_ports(self):
        if self._inventory.load_from == "live":
            return self._inventory.get_inventory_objects_under_dn(dn=self.dn, object_class=UcsImcNetworkAdapterPort,
                                                                  parent=self)
        elif self._inventory.load_from == "file" and "ports" in self._ucs_sdk_object:
            return [UcsImcNetworkAdapterPort(self, port) for port in self._ucs_sdk_object["ports"]]
        else:
            return []


class UcsImcHbaAdapter(UcsAdaptor, UcsImcInventoryObject):
    _UCS_SDK_OBJECT_NAME = "pciEquipSlot"
    _UCS_SDK_CATALOG_OBJECT_NAME = "pidCatalogPCIAdapter"
    _UCS_SDK_CATALOG_IDENTIFY_ATTRIBUTE = "slot"
    _UCS_SDK_OBJECT_IDENTIFY_ATTRIBUTE = "id"

    def __init__(self, parent=None, pci_equip_slot=None):
        UcsAdaptor.__init__(self, parent=parent, adaptor_unit=pci_equip_slot)

        self.id = self.get_attribute(ucs_sdk_object=pci_equip_slot, attribute_name="id")
        self.option_rom_status = self.get_attribute(ucs_sdk_object=pci_equip_slot, attribute_name="option_rom_status")
        self.pci_slot = self.get_attribute(ucs_sdk_object=pci_equip_slot, attribute_name="controller_reported",
                                           attribute_secondary_name="pci_slot")
        self.type = "hba"
        self.vendor = self.get_attribute(ucs_sdk_object=pci_equip_slot, attribute_name="vendor")
        self.version = self.get_attribute(ucs_sdk_object=pci_equip_slot, attribute_name="version")

        UcsImcInventoryObject.__init__(self, parent=parent, ucs_sdk_object=pci_equip_slot)

        self.short_name = None
        if self._inventory.load_from == "live":

            # Handling potentially incomplete UCS catalog entries (when SKU is not found)
            if self.sku is None:
                if hasattr(self, "_pid_catalog"):
                    if all(hasattr(self._pid_catalog, attr) for attr in ["vendor", "device", "subvendor", "subdevice"]):
                        vendor = self._pid_catalog.vendor
                        device = self._pid_catalog.device
                        subvendor = self._pid_catalog.subvendor
                        subdevice = self._pid_catalog.subdevice

                        self._determine_adaptor_sku_and_type(vendor=vendor, device=device, subvendor=subvendor,
                                                             subdevice=subdevice)

            self.short_name = self._get_model_short_name()

        elif self._inventory.load_from == "file":
            for attribute in ["short_name"]:
                setattr(self, attribute, None)
                if attribute in pci_equip_slot:
                    setattr(self, attribute, self.get_attribute(ucs_sdk_object=pci_equip_slot,
                                                                attribute_name=attribute))
