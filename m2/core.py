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
    # TODO Should check if entity is in quota table before proceeding
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
        self.config = config
        self.entity_id = entity.id
        self.entity_is_admin = entity.is_admin

        try:
            self.db.quota.get_info(self.entity_id)
        except Exception:
            self.db.quota.insert(self.entity_id, self.config.quota_default)

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

            # Skip Check if admin
            if not self.entity_is_admin:
                # Check whether entity has access to node and network
                self.driver.authorization. \
                    check_entity_node_access(self.entity_id, mac_address)
                self.driver.authorization. \
                    check_entity_network_access(self.entity_id, network_id)

                # Check whether entity owns image
                # TODO make into helper function
                if self.db.image.get_info(parent_image_id).entityId \
                        != self.entity_id:
                    raise Exception

            # What About Admin? I feel that his/her entity_id should be used,
            # instead of the user's.
            # Create new Provisioned Instance in DB
            instance_id = self.db.provisionedInstance.insert(instance_name,
                                                             instance_type,
                                                             self.entity_id,
                                                             network_id)

            # Setup Network by starting container or doing something magical!
            self.driver.multi_tenancy.setup(instance_id, network_id)

            # Create a clone (copy on write) of the parent image and associate
            # with the instance
            self.driver.storage.clone(instance_id, parent_image_id)

            # Get Clone Name and Other information needed for diskless
            clone_info = self.driver.storage.get_clone_info(instance_id)

            # Add Target To Diskless
            self.driver.diskless.add_target(instance_id, clone_info)

            # Generates the mac address and ipxe files.
            self.driver.diskless.register(instance_id, mac_address, nic)

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

            if not self.entity_is_admin:
                self.driver.authorization. \
                    check_entity_node_access(self.entity_id, dest_mac_address)

                if self.db.provisionedInstance.get_info(instance_id).entityId \
                        != self.entity_id:
                    raise Exception

            # Delete Mac Address and IPXE files associated with id
            self.driver.diskless.unregister(instance_id)
            # Register target with new node
            self.driver.diskless.register(instance_id, dest_mac_address,
                                          dest_nic)

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

            if not self.entity_is_admin:
                if instance.entityId != self.entity_id:
                    raise Exception

            self.driver.diskless.unregister(instance_id)
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

            if not self.entity_is_admin:
                if self.db.provisionedInstance.get_info(instance_id).entityId \
                        != self.entity_id:
                    raise Exception

            parent_image_id = self.db.provisionedInstance. \
                get_info(instance_id).parent_image_id

            # Insert Snapshot into DB
            parent_image = self.db.image.get_info(parent_image_id)
            snap_image_id = self.db.image.insert(snap_name, self.entity_id,
                                                 parent_image.type,
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
            if not self.entity_is_admin:
                if self.db.provisionedInstance.get_info(instance_id).entityId \
                        != self.entity_id:
                    raise Exception

            # Insert new Tag
            tag_id = self.db.tag.insert(tag_name, instance_id)

            # Create Tag
            self.driver.storage.tag(instance_id, tag_id)

            return tag_id

        except Exception:
            pass

    def update_tag(self, tag_id, info):
        """
        Update Tag Meta Data

        :param tag_id: Id of the tag
        :param info: Dictionary of new info that needs to be updated
        :return: None
        """

        try:

            if not self.entity_is_admin:

                instance_id = self.db.tag.get_info(tag_id).instance_id

                if self.db.provisionedInstance.get_info(instance_id).entityd \
                        != self.entity_id:
                    raise Exception

            # Update Tag
            self.db.tag.update(tag_id, info)

        except Exception:
            pass

    def delete_tag(self, tag_id):
        """
        Delete Tag

        :param tag_id: Id of the tag
        :return: None
        """

        try:

            if not self.entity_is_admin:
                instance_id = self.db.tag.get_info(tag_id).instance_id

                if self.db.provisionedInstance.get_info(instance_id).entityd \
                        != self.entity_id:
                    raise Exception

            # Delete Tag in Storage and in db.
            self.driver.storage.delete_tag(tag_id)
            self.db.tag.delete(tag_id)

        except Exception:
            pass

    def list_tags(self, instance_id):
        """
        List Tags for provisioned instance

        :param instance_id: Id of provisioned instance
        :return: List of tag_ids
        """

        try:

            if not self.entity_is_admin:
                if self.db.provisionedInstance.get_info(instance_id).entityId \
                        != self.entity_id:
                    raise Exception

            # Just Get tag objects from db and return
            return self.db.provisionedInstance.get_tags(instance_id)

        except Exception:
            pass

    def show_tag(self, tag_id):
        """
        Get Tag Meta Data

        :param tag_id: Id of the tag
        :return: Tag Info as an object
        """

        try:

            # Get Tag Info
            tag_info = self.db.tag.get_info(tag_id)

            if not self.entity_is_admin:

                if self.db.provisionedInstance.get_info(tag_info.instance_id) \
                        .entity_id != self.entity_id:
                    raise Exception

            return tag_info

        except Exception:
            pass

    def flatten_tag(self, tag_id, image_name):
        """
        Flatten Tag into an image

        :param tag_id: Id of the tag
        :param image_name: name of the flattened image
        :return: Image Id
        """

        try:

            instance_id = self.db.tag.get_info(tag_id).instance_id

            if not self.entity_is_admin:

                if self.db.provisionedInstance.get_info(instance_id).entityd \
                        != self.entity_id:
                    raise Exception

            # Get Parent Image
            parent_image_id = self.db.provisionedInstance. \
                get_info(instance_id).parent_image_id
            parent_image = self.db.image.get_info(parent_image_id)

            # Insert Image
            image_id = self.db.image.insert(image_name, self.entity_id,
                                            parent_image.type)

            # Flatten Tag
            self.driver.storage.flatten_tag(tag_id, image_id)

            return image_id

        except Exception:
            pass

    def list_instances(self):
        """
        List all provisioned instances for entity

        :return: List of instance objects
        """

        try:
            if self.entity_is_admin:
                self.db.provisionedInstance.get_all()
            else:
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

            if not self.entity_is_admin:
                if instance.entityId != self.entity_id:
                    raise Exception

            return instance

        except Exception:
            pass

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
    # Ignoring Datastore Id for now as There is no datastore_id in schema,
    # Can use it when schema is updated
    # Get Supported Types can be implemented in rest as it is a constansts list
    # Get Supported Types is needed for upload.
    def upload(self, image_name, image_type, datastore_id=None,
               is_public=False,
               url=None):
        """
        Upload image to M2

        :param image_name: Name of the image
        :param image_type: Type of the image
        :param datastore_id: Id of the datastore (Ignored)
        :param is_public: True if public
        :param url: HTTP url of the image
        :return: Id of the image
        """
        pass

    # Didnt figure out from last time how to implement this.
    def download(self, image_id):
        """
        Download image

        :param image_id: Id of the image
        :return: Byte stream
        """
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

            image = self.db.image.get_info(image_id)

            # Check If dest_entity_id is set then check if admin and execute
            if dest_entity_id is not None and self.entity_is_admin:
                new_image_id = self.db.image.insert(dest_image_name,
                                                    dest_entity_id,
                                                    image.type)

            elif dest_entity_id is not None and self.entity_is_admin \
                    and self.entity_id != dest_entity_id:
                raise Exception

            else:
                # Check If entity has access to image
                if image.entityId != self.entity_id:
                    raise Exception

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

            if not self.entity_is_admin:
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

            if not self.entity_is_admin:
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

            if self.entity_is_admin:
                return self.db.image.get_all()
            else:
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

            if not self.entity_is_admin:
                if self.db.image.get_info(image_id).entityId != self.entity_id:
                    raise Exception

            return self.db.image.get_info(image_id)

        except Exception:
            pass
