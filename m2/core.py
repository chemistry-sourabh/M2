# TODO Enforce Quota Checks
# TODO Check Authorization
class Core:

    # Wrote for probably working with middleware only
    def __init__(self, entityId, config):
        # Load Drivers
        # self.diskless = load_driver("Diskless", config)
        # self.diskless.storage = load_driver("DisklessStorage", config)
        self.diskless = None
        # self.storage = load_driver("Storage", config)
        self.storage = None
        # self.multi_tenancy = load_driver("MultiTenancy", config)
        self.multi_tenancy = None
        # self.authorization = load_driver("Authorization", config)
        self.authorization = None

        # self.db = Database(config)
        self.db = None

        self.entityId = entityId
        self.config = config

    def provision(self, instance_name, parent_image_id, mac_address, nic,
                  # provision_engine_id, Do we really need this ?
                  instance_type, network_id=None, extra_params=None):
        """
        Provisions a node with MAC address and nic with given image id

        :param instance_name:
        :param parent_image_id:
        :param mac_address:
        :param nic:
        :param instance_type:
        :param network_id:
        :param extra_params:
        :return: Provisioned Instance Id
        """

        try:
            self.authorization.check_entity_node_access(self.entityId,
                                                        mac_address)
            self.authorization.check_entity_network_access(self.entityId,
                                                           network_id)

            if self.db.image.get_info(
                    parent_image_id).entityId != self.entityId:
                raise Exception

            instance_id = self.db.provisionedInstance.insert(instance_name,
                                                             instance_type,
                                                             self.entityId,
                                                             network_id)

            self.multi_tenancy.setup(instance_id, network_id)

            clone_id = self.storage.clone(instance_id,
                                          parent_image_id)  # creates

            # Should do something for diskless depends on diskless driver
            # Nothing for TGT
            # map for IET if Ceph
            # self.storage.setup_clone(clone_id, self.config.diskless.key)

            # For IET Will need mapped rbd path
            # For TGT Will need clone name
            # What if interface is provided by diskless ?
            # clone_name = self.storage.get_clone_name(clone_id)
            target_id = self.diskless.mount_clone(instance_id,
                                                  clone_id)  # creates

            # Will the pxe stuff depend on the type of diskless provisioning
            # being used ?
            # IPXE template has diskless dependent line
            self.pxe.register(instance_id, mac_address, target_id, nic,
                              self.config.diskless.key)

            return instance_id

        except Exception:
            pass

    def migrate(self, instance_id, dest_mac_address, dest_nic):
        """

        :param instance_id:
        :param dest_mac_address:
        :param dest_nic:
        :return: None
        """

        try:

            self.authorization.check_entity_node_access(self.entityId,
                                                        dest_mac_address)

            if self.db.provisionedInstance.get_info(
                    instance_id).entityId != self.entityId:
                raise Exception

            target_id = self.diskless.get_target_id(instance_id)
            self.pxe.unregister(instance_id)
            self.pxe.register(instance_id, dest_mac_address, target_id,
                              dest_nic, self.config.diskless.key)

        except Exception:
            pass

    def deprovision(self, instance_id):
        """

        :param instance_id:
        :return: None
        """

        try:
            instance = self.db.provisionedInstance.get_info(instance_id)
            if instance.entityId != self.entityId:
                raise Exception

            self.pxe.unregister(instance_id)
            self.diskless.unmount_clone(instance_id)
            self.storage.delete_clone(instance_id)
            self.multi_tenancy.teardown(instance.network_id)
            self.db.provisionedInstance.delete(instance_id)

        except Exception:
            pass

    def snapshot(self, instance_id, snap_name):
        """

        :param instance_id:
        :param snap_name:
        :return:
        """

        try:
            if self.db.provisionedInstance.get_info(
                    instance_id).entityId != self.entityId:
                raise Exception

            clone = self.storage.get_clone_info(instance_id)
            image = self.storage.get_image_info(clone.parent_image_id)

            global_image = self.db.image.get_info(image.image_id)

            snap_image_id = self.db.image.insert(snap_name, self.entityId,
                                                 global_image.type,
                                                 isSnapshot=True)

            self.storage.snapshot(instance_id, snap_image_id)

            return snap_image_id

        except Exception:
            pass
