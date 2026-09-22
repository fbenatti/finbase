# coding: utf-8
# Copyright (c) 2016, 2026, Oracle and/or its affiliates.  All rights reserved.
# This software is dual-licensed to you under the Universal Permissive License (UPL) 1.0 as shown at https://oss.oracle.com/licenses/upl or Apache License 2.0 as shown at http://www.apache.org/licenses/LICENSE-2.0. You may choose either license.

from oci._vendor import six

import abc
import base64
import os
from cryptography.hazmat.primitives.ciphers import algorithms

from oci import regions
from oci._vendor.requests.exceptions import RequestException
from oci.config import REGION_ENV_VAR_NAME
from oci.exceptions import ServiceError
from oci.key_management.models import KeyShape, GenerateKeyDetails, DecryptDataDetails
from oci.key_management import KmsCryptoClient, KmsManagementClient, KmsVaultClient
from oci.encryption.internal.utils import (
    convert_to_str,
    verify_crc32_checksum,
    raise_runtime_error_from
)


@six.add_metaclass(abc.ABCMeta)
class MasterKeyProvider(object):
    """
    An abstract base class defining methods to vend MasterKeys
    for use in encryption and decryption.
    """
    @abc.abstractmethod
    def get_primary_master_key(self):
        """
        Returns the primary master key for this MasterKeyProvider.

        :rtype: oci.encryption.MasterKey
        """
        pass

    @abc.abstractmethod
    def get_master_key(self, **kwargs):
        """
        Returns a specific master key based on the arguments provided.

        :rtype: oci.encryption.MasterKey
        """
        pass


class KMSMasterKeyProvider(MasterKeyProvider):
    def __init__(self, config, kms_master_keys=None, **kwargs):
        """
        :param dict config: (required)
            An OCI config dict used to create underlying clients to talk to OCI KMS.
            Note, the 'region' in this config must match the region that the key / vault
            exist in otherwise they will not be found.

        :param list[KMSMasterKey] kms_master_keys: (optional)
            A list of KMSMasterKeys. Currently a max of 1 master key is supported.
            For decryption, you can use a KMSMasterKeyProvder with no master keys.

        :param signer: (optional)
            The signer to use when signing requests made by the service client. The default is to use a :py:class:`~oci.signer.Signer` based on the values
            provided in the config parameter.

            One use case for this parameter is for `Instance Principals authentication <https://docs.oracle.com/iaas/Content/Identity/Tasks/callingservicesfrominstances.htm>`__
            by passing an instance of :py:class:`~oci.auth.signers.InstancePrincipalsSecurityTokenSigner` as the value for this keyword argument
        :type signer: :py:class:`~oci.signer.AbstractBaseSigner`
        """
        if kms_master_keys is not None and len(kms_master_keys) > 1:
            raise ValueError(
                "Only one KMS master key is currently supported for KMSMasterKeyProvider"
            )

        self.primary_master_key = None
        if kms_master_keys:
            self.primary_master_key = kms_master_keys[0]

        self.signer = kwargs.get('signer')
        self.config = config
        if not self.config and not self.signer:
            raise ValueError(
                "Either a config or signer must be passed in"
            )

    def get_primary_master_key(self):
        return self.primary_master_key

    def get_master_key(self, **kwargs):
        """
        Return a KMSMasterKey based on the provided parameters.

        A configured primary KMSMasterKey pins the provider to its key, vault, and region. The
        master key identifier and any supplied vault or region must match. Without a primary key,
        a supplied region must match the provider configuration or be registered with the SDK.

        :param str master_key_id: (required)
            The OCID of this master key

        :param str vault_id: (optional)
            The OCID of the vault this master key resides in. When supplied to a pinned provider,
            it must match the configured master key's vault. Required when the provider needs to
            create a KMSMasterKey.

        :param str region: (optional)
            The region this master key resides in. When supplied to a pinned provider, it must
            match the configured master key's region. A provider without a master key accepts a
            supplied region only when it is configured or registered with the SDK.
        """
        master_key_id = kwargs.get("master_key_id")
        vault_id = kwargs.get("vault_id")
        region = kwargs.get("region")

        self._validate_supplied_string_argument("master_key_id", master_key_id, required=True)
        self._validate_supplied_string_argument(
            "vault_id",
            vault_id,
            required=self.primary_master_key is None,
        )
        self._validate_supplied_string_argument("region", region)

        if self.primary_master_key:
            self._validate_pinned_master_key(master_key_id, vault_id, region)
            return self.primary_master_key

        if region is not None:
            kwargs["region"] = self._resolve_trusted_region(region)

        master_key_config = self.config

        if self.signer:
            kwargs["signer"] = self.signer

        kms_master_key = KMSMasterKey(config=master_key_config, **kwargs)
        return kms_master_key

    @staticmethod
    def _validate_supplied_string_argument(name, value, required=False):
        if value is None:
            if required:
                raise ValueError("keyword argument {} must not be None".format(name))
            return

        if not isinstance(value, str) or not value:
            raise ValueError("keyword argument {} must be a non-empty string".format(name))

    @staticmethod
    def _normalize_region(region):
        if not isinstance(region, str):
            return None

        lowercase_region = region.lower()
        return regions.REGIONS_SHORT_NAMES.get(lowercase_region, lowercase_region)

    @classmethod
    def _regions_match(cls, first_region, second_region):
        first_normalized_region = cls._normalize_region(first_region)
        second_normalized_region = cls._normalize_region(second_region)
        return first_normalized_region is not None and (
            first_normalized_region == second_normalized_region
        )

    def _validate_pinned_master_key(self, master_key_id, vault_id, region):
        if self.primary_master_key.get_identifier() != master_key_id:
            raise ValueError("Requested master key ID does not match the configured KMS master key")

        if vault_id is not None and self.primary_master_key.vault_id != vault_id:
            raise ValueError("Requested vault ID does not match the configured KMS master key")

        if region is not None and not self._regions_match(self.primary_master_key.region, region):
            raise ValueError("Requested region does not match the configured KMS master key")

    def _resolve_trusted_region(self, region):
        configured_region = self.config.get("region") if self.config else None
        if configured_region is None:
            configured_region = os.environ.get(REGION_ENV_VAR_NAME)

        normalized_configured_region = self._normalize_region(configured_region)
        normalized_requested_region = self._normalize_region(region)

        if normalized_configured_region == normalized_requested_region:
            return normalized_configured_region

        try:
            is_trusted_region = regions.is_region(normalized_requested_region)
        except RequestException:
            raise ValueError("The requested region must be configured or registered with the SDK")

        if is_trusted_region:
            # Region metadata lookup may add a new short-name mapping.
            return self._normalize_region(normalized_requested_region)

        raise ValueError("The requested region must be configured or registered with the SDK")


@six.add_metaclass(abc.ABCMeta)
class MasterKey(object):
    """
    An abstract base class representing a MasterKey resource to be used in
    encryption and decryption operations.
    """
    @abc.abstractmethod
    def generate_data_encryption_key(self, algorithm):
        """
        Generates a data encryption key (DEK) based on the algorithm provided using
        this MasterKey.  The returned DataEncryptionKey includes a copy of the
        DEK encrypted under this MasterKey.

        :param oci.encryption.algorithms.Algorithm algorithm: (required)
            The algorithm the key will be used for.

        :rtype: oci.encryption.key_providers.DataEncryptionKey
        """
        pass

    @abc.abstractmethod
    def decrypt(self, bytes_to_decrypt):
        """
        Decrypts and returns bytes that were encrypted under this master key.

        :param bytes bytes_to_decrypt: (required)
            The bytes to decrypt using this MasterKey.

        :rtype: bytes
        """
        pass

    @abc.abstractmethod
    def get_identifier(self):
        """
        Returns an identifier for this MasterKey.

        :rtype: str
        """
        pass


class KMSMasterKey(MasterKey):
    def __init__(self, config, master_key_id, vault_id, **kwargs):
        """
        Represents a MasterKey contained in the OCI Key Management Service.

        :param dict config: (required)
            An OCI config dict used to create underlying clients to talk to OCI KMS.
            Note, the 'region' in this config must match the region that the key / vault
            exist in otherwise they will not be found.

        :param str master_key_id: (required)
            The OCID of the KMS master key

        :param str vault_id: (required)
            The OCID of the vault containing the master key

        :param signer: (optional)
            The signer to use when signing requests made by the service client. The default is to use a :py:class:`~oci.signer.Signer` based on the values
            provided in the config parameter.

            One use case for this parameter is for `Instance Principals authentication <https://docs.oracle.com/iaas/Content/Identity/Tasks/callingservicesfrominstances.htm>`__
            by passing an instance of :py:class:`~oci.auth.signers.InstancePrincipalsSecurityTokenSigner` as the value for this keyword argument
        :type signer: :py:class:`~oci.signer.AbstractBaseSigner`

        :param str region: (optional)
            The region this master key resides in
        """
        if not config and not kwargs.get("signer"):
            raise ValueError(
                "Either a config or signer must be passed in"
            )

        self.region = None
        # Get region from **kwargs, config, or signer
        if kwargs.get('region'):
            self.region = kwargs.get("region")
        elif "region" in config:
            self.region = config["region"]
        elif kwargs.get('signer'):
            self.region = kwargs.get("signer").region

        kms_vault_client = KmsVaultClient(config, **kwargs)
        # There is a chance that caller specified a region and differs from the config or signer's region
        kms_vault_client.base_client.set_region(self.region)

        try:
            vault = kms_vault_client.get_vault(vault_id).data
        except ServiceError as service_error:
            message = "Failed to access vaultId: {vault_id} while targeting region: {region}.".format(
                vault_id=vault_id, region=self.region
            )
            raise_runtime_error_from(message, service_error)

        self.kms_management_client = KmsManagementClient(
            config, service_endpoint=vault.management_endpoint, **kwargs
        )

        self.kms_crypto_client = KmsCryptoClient(
            config, service_endpoint=vault.crypto_endpoint, **kwargs
        )

        self.master_key_id = master_key_id
        self.vault_id = vault.id

    def generate_data_encryption_key(self, algorithm):
        dek_key_shape = KeyShape()

        # only AES is currently supported for client side encryption
        if algorithm.algorithm.name != algorithms.AES.name:
            raise ValueError(
                "Only AES is currently supported for client side encryption with KMS master key"
            )

        dek_key_shape.algorithm = KeyShape.ALGORITHM_AES
        dek_key_shape.length = algorithm.key_len

        generate_key_details = GenerateKeyDetails()
        generate_key_details.include_plaintext_key = True
        generate_key_details.key_id = self.master_key_id
        generate_key_details.key_shape = dek_key_shape

        try:
            generated_key = self.kms_crypto_client.generate_data_encryption_key(
                generate_key_details
            ).data
        except ServiceError as service_error:
            message = "Failed to generate data encryption key using masterKeyId: {master_key_id} while targeting vault: {vault_id}.".format(
                master_key_id=self.master_key_id,
                vault_id=self.vault_id
            )
            raise_runtime_error_from(message, service_error)

        dek_plaintext_bytes = base64.b64decode(generated_key.plaintext)
        dek_ciphertext_bytes = base64.b64decode(generated_key.ciphertext)

        return DataEncryptionKey(
            plaintext_key_bytes=dek_plaintext_bytes,
            encrypted_key_bytes=dek_ciphertext_bytes,
            plaintext_key_checksum=generated_key.plaintext_checksum,
        )

    def decrypt(self, bytes_to_decrypt):
        """
        Decrypts and returns bytes that were encrypted under this master key.

        :param bytes bytes_to_decrypt: (required)
            The bytes to decrypt using this MasterKey.

        :rtype: bytes
        """
        decrypt_data_details = DecryptDataDetails()

        # KMS API expects the key base64 encoded
        decrypt_data_details.ciphertext = convert_to_str(
            base64.b64encode(bytes_to_decrypt)
        )
        decrypt_data_details.key_id = self.master_key_id

        try:
            decrypted_data = self.kms_crypto_client.decrypt(decrypt_data_details).data
        except ServiceError as service_error:
            message = "Failed to decrypt data encryption key using masterKeyId: {master_key_id} while targeting vault: {vault_id}.".format(
                master_key_id=self.master_key_id,
                vault_id=self.vault_id
            )
            raise_runtime_error_from(message, service_error)

        verify_crc32_checksum(
            base64.b64decode(decrypted_data.plaintext),
            decrypted_data.plaintext_checksum,
        )

        return decrypted_data.plaintext

    def get_identifier(self):
        """
        Returns the OCID of this master key in the OCI Key Management Service.

        :rtype: str
        """
        return self.master_key_id


class DataEncryptionKey(object):
    """
    Represents a data encryption key used to encrypt and decrypt payloads.
    """
    def __init__(
        self, plaintext_key_bytes, encrypted_key_bytes, plaintext_key_checksum=None
    ):
        """
        :param bytes plaintext_key_bytes:
            The bytes of the data encryption key in plaintext

        :param bytes encrypted_key_bytes:
            The bytes of the data encrypted key encrypted under a master key

        :param str plaintext_key_checksum:
            The crc32 checsum of the plaintext data encryption key
        """
        self.plaintext_key_bytes = plaintext_key_bytes
        self.encrypted_key_bytes = encrypted_key_bytes
        self.plaintext_key_checksum = plaintext_key_checksum

        if self.plaintext_key_checksum:
            verify_crc32_checksum(plaintext_key_bytes, plaintext_key_checksum)
