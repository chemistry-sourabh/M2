""" Core of M2 which is the backend of the REST API """


# TODO Enforce Quota Checks
# TODO Check Authorization
# TODO Add Admin Level Stuff
class Core:
    """
    The class that will process a REST request.
    Currently will only work with middleware.
    """

    # Can be extended for DB Auth by doing some architecture change
    # Authentication Driver should be used in rest.py to get entity object
    # and passed here.
    def __init__(self, entity, config, driver):
        """
        Initializes DB and other parameters

        :param entity: Entity Object
        :param config: The Config Object
        :param driver: Object whose attributes are loaded driver objects
        """

        # Drivers should be loaded in init/startup script and passed here

        # Initialize DB
        # self.db = Database(config)
        self.db = None

        self.driver = driver
        self.entity_id = entity.id
        self.entity_is_admin = entity.is_admin
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

            # Should mount the clone on the diskless server and create a
            # target, there are 2 problems here:-

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
            # * The diskless storage driver is going to be passed as an
            # argument to diskless driver during init and the diskless driver
            # is going to use it in mount and unmount clone calls.
            target_id = self.driver.diskless.add_target(instance_id, clone_id)

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

            self.driver.authorization. \
                check_entity_node_access(self.entity_id, dest_mac_address)

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
            self.driver.diskless.delete_target(instance_id)
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
            parent_image_id = self.db.provisionedInstance. \
                get_info(instance_id).parent_image_id

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

    def tag(self, instance_id, tag_name):
        """
        Create a shallow copy of the provisioned instance's current state
        (filesystem only) by copying the clone.

        :param instance_id: Id of the provisioned instance
        :param tag_name: Name of the new tag
        :return: Tag Id
        """

        try:
            if self.db.provisionedInstance.get_info(instance_id).entityId \
                    != self.entity_id:
                raise Exception

            # Turns is_active bit for all tags under instance_id to False
            self.db.tag.deactivate_all_tags(instance_id)
            # Insert new Tag
            tag_id = self.db.tag.insert(tag_name, instance_id, is_active=True)

            # Create Tag
            self.driver.storage.tag(instance_id, tag_id)

            return tag_id

        except Exception:
            pass

    # Confusion about Tag commands regarding Ids. Need to discuss
    # Assuming that Id is going to be unique under a provisioned instance id
    def update_tag(self, instance_id, tag_id, info):
        """
        Update Tag Meta Data

        :param instance_id: Id of the provisioned instance
        :param tag_id: Id of the tag
        :param info: Dictionary of new info that needs to be updated
        :return: None
        """

        try:

            if self.db.provisionedInstance.get_info(instance_id).entityId \
                    != self.entity_id:
                raise Exception

            # Check If Tag Exists
            self.db.tag.get_info(instance_id, tag_id)

            # Update Tag
            self.db.tag.update(instance_id, tag_id, info)

        except Exception:
            pass

    def delete_tag(self, instance_id, tag_id):
        """
        Delete Tag

        :param instance_id: Id of the provisioned instance
        :param tag_id: Id of the tag
        :return: None
        """

        try:

            if self.db.provisionedInstance.get_info(instance_id).entityId \
                    != self.entity_id:
                raise Exception

            # Check If Tag Exists
            self.db.tag.get_info(instance_id, tag_id)

            # Delete Tag in Storage and in db.
            self.driver.storage.delete_tag(instance_id, tag_id)
            self.db.tag.delete(instance_id, tag_id)

        except Exception:
            pass

    def list_tags(self, instance_id):
        """
        List Tags for provisioned instance

        :param instance_id: Id of provisioned instance
        :return: List of tag_ids
        """

        try:

            if self.db.provisionedInstance.get_info(instance_id).entityId \
                    != self.entity_id:
                raise Exception

            # Just Get tag objects from db and return
            return self.db.provisionedInstance.get_tags(instance_id)

        except Exception:
            pass

    def show_tag(self, instance_id, tag_id):
        """
        Get Tag Meta Data

        :param instance_id: Id of the provisioned instance
        :param tag_id: Id of the tag
        :return: Tag Info as an object
        """

        try:

            if self.db.provisionedInstance.get_info(instance_id).entityId \
                    != self.entity_id:
                raise Exception

            # Get and return info
            return self.db.tag.get_info(instance_id, tag_id)

        except Exception:
            pass

    def flatten_tag(self, instance_id, tag_id, image_name):
        """
        Flatten Tag into an image

        :param instance_id: Id of the provisioned instance
        :param tag_id: Id of the tag
        :param image_name: name of the flattened image
        :return: Image Id
        """

        try:

            if self.db.provisionedInstance.get_info(instance_id).entityId \
                    != self.entity_id:
                raise Exception

            self.db.tag.get_info(instance_id, tag_id)

            # Get Parent Image, Check comments in snapshot
            parent_image_id = self.db.provisionedInstance. \
                get_info(instance_id).parent_image_id
            parent_image = self.db.image.get_info(parent_image_id)

            # Insert Image
            image_id = self.db.image.insert(image_name, self.entity_id,
                                            parent_image.type)

            # Flatten Tag
            self.driver.storage.flatten_tag(instance_id, tag_id, image_id)

            return image_id

        except Exception:
            pass

    def list_instances(self):
        """
        List all provisioned instances for entity

        :return: List of instance objects
        """

        try:

            return self.db.provisionedInstance.get_all(self.entity_id)

        except Exception:
            pass

    def show_instance(self, instance_id):
        """
        Get Instance Details

        :return: Instance Object
        """

        try:

            instance = self.db.provisionedInstance.get_info(instance_id)

            if instance.entityId != self.entity_id:
                raise Exception

            return instance

        except Exception:
            pass

    # Function should be added for inserting new entity into quota table.
    # Should check if entity is in quota table before proceeding
    # Add current_usage column to Quota to avoid long counting operations ?
    def update_quota(self, entity_id, info):
        """
        Update Entity's quota object with new info

        ADMIN OPERATION

        :param entity_id: Id of the entity that should be updated
        :param info: new info as dictionary
        :return: None
        """

        try:

            if self.entity_is_admin:
                self.db.quota.update(entity_id, info)
            else:
                raise Exception

        except Exception:
            pass

    def list_quota(self):
        """
        List quota of all registered entities

        ADMIN OPERATION

        :return: List of Quota Objects
        """

        try:

            if self.entity_is_admin:
                return self.db.quota.get_all()
            else:
                raise Exception

        except Exception:
            pass

    def show_quota(self, entity_id):
        """
        Get Quota of Registered Entity

        ADMIN OPERATION

        :param entity_id: Id of the entity
        :return: Quota Object
        """

        try:

            if self.entity_is_admin:
                return self.db.quota.get_info(entity_id)
            else:
                raise Exception

        except Exception:
            pass

    # This function doesnt exist in the REST API, but should
    def register(self, entity_id, quota):
        """
        Add Quota for Entity

        ADMIN OPERATION

        :param entity_id: Id of the entity
        :param quota: Quota of the entity
        :return: Id of the new quota
        """

        try:

            if self.entity_is_admin:
                return self.db.quota.insert(entity_id, quota)
            else:
                raise Exception

        except Exception:
            pass

    # Didnt figure out from last time how to implement this.
    # Do we want to support multiple datastores right now. For the first
    # prototype version we can implement a single datastore version
    def upload(self, image_name, image_type, datastore_id=None,
               is_public=False,
               url=None):
        pass

    # Didnt figure out from last time how to implement this.
    def download(self, image_id):
        pass

    def copy(self, image_id, dest_image_name, dest_entity_id=None,
             dest_datastore_id=None):
        """
        Do a deep copy of an image

        :param image_id: Id of the image to copy
        :param dest_image_name: name of the image copy
        :param dest_entity_id: Id of the destination entity (Admin Only)
        :param dest_datastore_id: Id of the destination datastore (Ignored)
        :return: Id of the copied image
        """

        try:

            # Check If entity has access to image
            image = self.db.image.get_info(image_id)
            if image.entityId != self.entity_id:
                raise Exception

            # Check If dest_entity_id is set then check if admin and execute
            if dest_entity_id is not None and self.entity_id != dest_entity_id:
                if self.entity_is_admin:
                    new_image_id = self.db.image.insert(dest_image_name,
                                                        dest_entity_id,
                                                        image.type)

                else:
                    raise Exception

            else:
                new_image_id = self.db.image.insert(dest_image_name,
                                                    self.entity_id,
                                                    image.type)

            # After DB insertion do the actual copy
            self.driver.storage.copy(image_id, new_image_id)
            return new_image_id

        except Exception:
            pass

    def update_image(self, image_id, info):
        """
        Update metadata of image

        :param image_id: Id of the image
        :param info: New info as a dictionary
        :return: None
        """
        try:
            if self.db.image.get_info(image_id).entityId != self.entity_id:
                raise Exception

            self.db.image.update(image_id, info)

        except Exception:
            pass

    def delete_image(self, image_id):
        """
        Delete image

        :param image_id: Id of the image
        :return: None
        """
        try:
            if self.db.image.get_info(image_id).entityId != self.entity_id:
                raise Exception

            self.driver.storage.delete_image(image_id)
            self.db.image.delete(image_id)

        except Exception:
            pass

    def list_images(self):
        """
        List all images of an entity

        :return: List of Image objects
        """

        try:

            return self.db.image.get_all(self.entity_id)

        except Exception:
            pass

    def show_image(self, image_id):
        """
        Get Details of image

        :param image_id: Id of the image
        :return: Image object
        """

        try:
            if self.db.image.get_info(image_id).entityId != self.entity_id:
                raise Exception

            return self.db.image.get_info(image_id)

        except Exception:
            pass

    # Get Supported Types can be implemented in rest as it is a constansts list
    # How to implement List Datastores and List Provisioning Engines
