""" Core of M2 which is the backend of the REST API"""


# TODO Enforce Quota Checks
# TODO Check Authorization
class Core:
    """
    The class that will process a REST request.
    Currently will only work with middleware.
    """

    def __init__(self, entity_id, config, driver):
        """
        Initializes DB and other parameters

        :param entity_id: The Id of the entity that has sent the request.
        :param config: The Config Object
        :param driver: Object whose attributes are loaded driver objects
        """

        # Drivers should be loaded in init/startup script and passed here

        # Initialize DB
        # self.db = Database(config)
        self.db = None

        self.driver = driver
        self.entity_id = entity_id
        self.config = config

    # provision_engine_id is in rest_api.md, but do we really need it ?
    # Not sure where the extra_params are required, we can leave it as it is
    # for now and update later if required
    def provision(self, instance_name, parent_image_id, mac_address, nic,
                  instance_type, network_id=None, extra_params=None):
        """
        Provisions a node with MAC address and nic with given image id

        :param instance_name: Name of the new instance
        :param parent_image_id: Image Id of the golden image
        :param mac_address: MAC address of the node that should be provisioned
        :param nic: BIOS NIC name that should be used to provision
        :param instance_type: BM (Bare Metal) or VM
        :param network_id: Id of the network that has the node
        :param extra_params: Any extra params that may be required (Ignored)
        :return: Provisioned Instance Id
        """

        try:

            # Check whether entity has access to node and network
            self.driver.authorization.check_entity_node_access(self.entity_id,
                                                               mac_address)
            self.driver.authorization.check_entity_network_access(
                self.entity_id,
                network_id)

            # Check whether entity owns image
            # TODO make into helper function
            if self.db.image.get_info(parent_image_id).entityId \
                    != self.entity_id:
                raise Exception

            # Create new Provisioned Instance in DB
            instance_id = self.db.provisionedInstance.insert(instance_name,
                                                             instance_type,
                                                             self.entity_id,
                                                             network_id)

            # Setup Network by starting container or doing something magical!
            self.driver.multi_tenancy.setup(instance_id, network_id)

            # Create a clone (copy on write) of the parent image and associate
            # with the instance
            clone_id = self.driver.storage.clone(instance_id, parent_image_id)

            # Should mount the clone on the diskless server and create a target,
            #  there are 2 problems here:-

            # 1. Some operations may need to be done to setup clone like for
            # example a rbd map needs to be done for IET.

            # 2. The clone information needed depends on diskless like for
            # example, TGT needs clone name whereas IET needs mapped rbd path.

            # To solve this
            # * Each diskless driver should provide another interface called
            # Diskless Storage interface that either the storage driver or some
            # other class can implement.
            # * There should a Diskless Storage driver for every
            # diskless-storage combination we want to support.
            # * The diskless storage driver is going to be passed as an argument
            # to diskless driver during init and the diskless driver is going
            # to use it in mount and unmount clone calls.
            target_id = self.driver.diskless.mount_clone(instance_id, clone_id)

            # I dont see a reason yet to write a pxe driver as PXE, DHCP and
            # TFTP are all protocols and driver is just going to generate files
            # that follow a template. We can hardcode a driver in the driver
            # loading phase and pass a key (self.config.diskless.key) that will
            # tell the driver which template to use as the template is diskless
            # dependent. Incase we need a driver in the future should be less
            # than 10 lines.
            # Generates the mac address and ipxe files.
            self.driver.pxe.register(instance_id, mac_address, target_id, nic,
                                     self.config.diskless.key)

            return instance_id

        except Exception:
            pass

    def migrate(self, instance_id, dest_mac_address, dest_nic):
        """
        Associate a clone of parent image with another node.

        :param instance_id: Id of provisioned instance
        :param dest_mac_address: MAC address of New Node
        :param dest_nic: NIC of New Node
        :return: None
        """

        try:

            self.driver.authorization.check_entity_node_access(self.entity_id,
                                                               dest_mac_address)

            if self.db.provisionedInstance.get_info(instance_id).entityId \
                    != self.entity_id:
                raise Exception

            # Get Target Id associated with instance id
            target_id = self.driver.diskless.get_target_id(instance_id)
            # Delete Mac Address and IPXE files associated with id
            self.driver.pxe.unregister(instance_id)
            # Register target with new node
            self.driver.pxe.register(instance_id, dest_mac_address, target_id,
                                     dest_nic, self.config.diskless.key)

        except Exception:
            pass

    def deprovision(self, instance_id):
        """
        Deprovisions a node with given instance id

        :param instance_id: id of the instance should be deprovisioned
        :return: None
        """

        try:
            instance = self.db.provisionedInstance.get_info(instance_id)
            if instance.entityId != self.entity_id:
                raise Exception

            self.driver.pxe.unregister(instance_id)
            # Unmount Clone from diskless
            self.driver.diskless.unmount_clone(instance_id)
            # Delete Clone in Storage
            self.driver.storage.delete_clone(instance_id)
            # Delete Container if not being used.
            self.driver.multi_tenancy.teardown(instance.network_id)
            # Delete entry in DB
            self.db.provisionedInstance.delete(instance_id)

        except Exception:
            pass

    def snapshot(self, instance_id, snap_name):
        """
        Create a deep copy of the provisioned instance's current state
        (filesystem only) by copying the clone.

        :param instance_id: Id of the provisioned instance
        :param snap_name: Name of the new snapshot
        :return: Snapshot image id
        """

        try:
            if self.db.provisionedInstance.get_info(instance_id).entityId \
                    != self.entity_id:
                raise Exception

            # This is how it is currently done, but shouldnt be as we are
            # dictating that the clone should have a parent_image_id in the
            # driver
            # clone = self.driver.storage.get_clone_info(instance_id)
            # image = self.driver.storage.get_image_info(clone.parent_image_id)
            # parent_image_id = image.image_id

            # This is not possible currently as provisioned instance table
            # doesnt have a foreign key to image table, but I feel this is the
            # right way.
            parent_image_id = self.db.provisionedInstance.get_info(instance_id) \
                .parent_image_id

            # Insert Snapshot into DB
            global_image = self.db.image.get_info(parent_image_id)
            snap_image_id = self.db.image.insert(snap_name, self.entity_id,
                                                 global_image.type,
                                                 isSnapshot=True)

            # Take Snapshot
            self.driver.storage.snapshot(instance_id, snap_image_id)

            return snap_image_id

        except Exception:
            pass
